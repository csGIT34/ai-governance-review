import copy
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import checklist
from app.db import blob, cosmos
from app.enrichment import apply, azure_catalog, gcp_catalog, reference_docs

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cosmos.ensure_resources()
    yield


app = FastAPI(title="AI Governance Review", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def add_artifact(doc: dict, name: str, data: bytes):
    """Upload to blob and upsert the artifact entry on the review doc."""
    blob_path = blob.upload_artifact(doc["id"], name, data)
    doc["artifacts"] = [a for a in doc["artifacts"] if a["name"] != name]
    doc["artifacts"].append({
        "name": name,
        "blob_path": blob_path,
        "size": len(data),
        "uploaded_at": now(),
    })


def blocker_gaps(doc: dict) -> list[dict]:
    """Blocker items not yet Pass or N/A."""
    return [
        item for item in checklist.ITEMS
        if item["severity"] == "blocker"
        and doc["checklist"].get(item["id"], {}).get("status") not in ("pass", "na")
    ]


def progress(doc: dict) -> dict:
    total = len(doc["checklist"])
    done = sum(1 for v in doc["checklist"].values() if v["status"] != "unreviewed")
    return {"done": done, "total": total}


def source_groups(doc: dict) -> list[dict]:
    """Checklist items grouped by data source, with open (unreviewed) counts."""
    groups = []
    for src in checklist.SOURCES:
        items = [i for i in checklist.ITEMS if i["source"] == src["id"]]
        open_ids = [i["id"] for i in items
                    if doc["checklist"].get(i["id"], {}).get("status") == "unreviewed"]
        groups.append({**src, "items": items, "open_ids": open_ids})
    return groups


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/catalog-models")
def catalog_models(csp: str, region: str = "", publisher: str = "google"):
    """Model ids for the new-review form pickers, from the live CSP catalog."""
    if csp == "azure":
        first_region = (region or "eastus").split(",")[0].strip() or "eastus"
        return JSONResponse(azure_catalog.list_models(first_region))
    return JSONResponse(gcp_catalog.list_models(publisher))


@app.get("/")
def index(request: Request):
    reviews = cosmos.list_reviews()
    for r in reviews:
        r["progress"] = progress(r)
    return templates.TemplateResponse(request, "index.html", {"reviews": reviews})


@app.get("/api/check-url")
def check_url(url: str):
    """Settings page 'Check' button: verify a reference-doc URL loads, using
    the same fetch the snapshotter uses."""
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "detail": "URL must start with http(s)://"}
    try:
        reference_docs.fetch(url)
        return {"ok": True, "detail": "page loads"}
    except Exception as err:
        return {"ok": False, "detail": str(err)}


@app.get("/settings")
def settings_page(request: Request, error: str = ""):
    return templates.TemplateResponse(request, "settings.html", {
        "docs": reference_docs.get_docs(),
        "customized": cosmos.get_settings("reference-docs") is not None,
        "error": error,
    })


def _save_ref_docs(docs: dict):
    cosmos.save_settings({"id": "reference-docs", "csp": "_settings",
                          "docs": docs, "updated_at": now()})


def _parse_item_ids(raw: str) -> tuple[list[str], list[str]]:
    """Comma-separated checklist ids -> (ids, unknown ids)."""
    ids = [s.strip().upper() for s in raw.split(",") if s.strip()]
    known = {i["id"] for i in checklist.ITEMS}
    return ids, [i for i in ids if i not in known]


@app.post("/settings/reference-docs/save")
async def save_reference_docs(request: Request):
    form = await request.form()
    csp = form.get("csp", "")
    docs = copy.deepcopy(reference_docs.get_docs())
    for ref in docs.get(csp, []):
        slug = ref["slug"]
        if f"url__{slug}" not in form:
            continue
        url = form.get(f"url__{slug}", "").strip()
        if not url.startswith(("http://", "https://")):
            return RedirectResponse(
                f"/settings?error={ref['title']}: URL must start with http(s)://",
                status_code=303)
        ids, unknown = _parse_item_ids(form.get(f"items__{slug}", ""))
        if unknown:
            return RedirectResponse(
                f"/settings?error={ref['title']}: unknown checklist items "
                f"{', '.join(unknown)}", status_code=303)
        ref["url"] = url
        ref["title"] = form.get(f"title__{slug}", "").strip() or ref["title"]
        ref["items"] = ids
    _save_ref_docs(docs)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/reference-docs/add")
def add_reference_doc(csp: str = Form(...), title: str = Form(...),
                      url: str = Form(...), items: str = Form("")):
    if not url.strip().startswith(("http://", "https://")):
        return RedirectResponse("/settings?error=URL must start with http(s)://",
                                status_code=303)
    ids, unknown = _parse_item_ids(items)
    if unknown:
        return RedirectResponse(
            f"/settings?error=Unknown checklist items: {', '.join(unknown)}",
            status_code=303)
    docs = copy.deepcopy(reference_docs.get_docs())
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "doc"
    existing, base, n = {d["slug"] for d in docs.get(csp, [])}, slug, 2
    while slug in existing:
        slug, n = f"{base}-{n}", n + 1
    docs.setdefault(csp, []).append(
        {"slug": slug, "title": title.strip(), "url": url.strip(), "items": ids})
    _save_ref_docs(docs)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/reference-docs/delete")
async def delete_reference_doc(request: Request):
    form = await request.form()
    csp, slug = form.get("csp", ""), form.get("delete_slug", "")
    docs = copy.deepcopy(reference_docs.get_docs())
    docs[csp] = [d for d in docs.get(csp, []) if d["slug"] != slug]
    _save_ref_docs(docs)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/reference-docs/reset")
def reset_reference_docs():
    cosmos.delete_settings("reference-docs")
    return RedirectResponse("/settings", status_code=303)


@app.get("/reviews/new")
def new_review_form(request: Request):
    return templates.TemplateResponse(request, "new.html",
                                      {"gcp_publishers": gcp_catalog.PUBLISHERS})


@app.post("/reviews")
def create_review(
    csp: str = Form(...),
    service_name: str = Form(...),
    model_name: str = Form(...),
    model_version: str = Form(""),
    publisher: str = Form(""),
    regions: str = Form(""),
    requested_by: str = Form(""),
    justification: str = Form(""),
):
    doc = {
        "id": str(uuid.uuid4()),
        "csp": csp,
        "service_name": service_name,
        "model_name": model_name,
        "model_version": model_version,
        "publisher": publisher,
        "regions": regions,
        "requested_by": requested_by,
        "justification": justification,
        "status": "in_review",
        "checklist_version": checklist.CHECKLIST_VERSION,
        "checklist": checklist.new_checklist(),
        "enrichment": None,
        "artifacts": [],
        "decision": None,
        "created_at": now(),
        "updated_at": now(),
    }
    cosmos.save_review(doc)
    return RedirectResponse(f"/reviews/{doc['id']}", status_code=303)


@app.get("/reviews/{review_id}")
def review_detail(request: Request, review_id: str, error: str = ""):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    docs = reference_docs.get_docs()
    return templates.TemplateResponse(request, "review.html", {
        "r": doc,
        "source_groups": source_groups(doc),
        "ref_docs": docs.get(doc["csp"], []),
        "internal_docs": docs.get("internal", []),
        "statuses": checklist.STATUSES,
        "blocker_gaps": blocker_gaps(doc),
        "progress": progress(doc),
        "error": error,
    })


@app.post("/reviews/{review_id}/checklist")
async def save_checklist(request: Request, review_id: str):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    form = await request.form()
    for item in checklist.ITEMS:
        iid = item["id"]
        if f"status__{iid}" in form:
            doc["checklist"][iid] = {
                "status": form.get(f"status__{iid}", "unreviewed"),
                "notes": form.get(f"notes__{iid}", ""),
                "evidence": form.get(f"evidence__{iid}", ""),
            }
    doc["updated_at"] = now()
    cosmos.save_review(doc)
    return RedirectResponse(f"/reviews/{review_id}", status_code=303)


@app.post("/reviews/{review_id}/enrich")
def enrich(review_id: str):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    region = (doc.get("regions") or "eastus").split(",")[0].strip()
    if doc["csp"] == "azure":
        snapshot = azure_catalog.enrich(doc["model_name"], region)
    else:
        snapshot = gcp_catalog.enrich(doc["model_name"], doc.get("publisher") or "google")
    if not snapshot.get("error"):
        applier = apply.apply_azure if doc["csp"] == "azure" else apply.apply_gcp
        snapshot["checklist_updates"] = applier(snapshot, doc)
    doc["enrichment"] = snapshot
    if not snapshot.get("error"):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        add_artifact(doc, f"catalog-snapshot-{stamp}.json",
                     json.dumps(snapshot, indent=2).encode())
    doc["updated_at"] = now()
    cosmos.save_review(doc)
    return RedirectResponse(f"/reviews/{review_id}", status_code=303)


@app.post("/reviews/{review_id}/reference-docs")
def fetch_reference_docs(review_id: str):
    """Snapshot the CSP's data-privacy/terms pages into artifacts and link
    them as evidence on the related, still-unreviewed checklist items."""
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    docs = reference_docs.get_docs()
    # Internal documents are usually behind sign-in: link as evidence, no snapshot.
    for ref in docs.get("internal", []):
        for iid in ref["items"]:
            resp = doc["checklist"].get(iid)
            if (resp and resp["status"] == "unreviewed" and not resp["evidence"]):
                resp["evidence"] = f"{ref['title']} ({ref['url']})"
    problems = []
    for ref in docs.get(doc["csp"], []):
        try:
            html = reference_docs.fetch(ref["url"])
        except Exception as err:
            problems.append(f"{ref['title']}: {err}")
            continue
        changed = (ref.get("validated_hash")
                   and reference_docs.text_hash(html) != ref["validated_hash"])
        if changed:
            problems.append(f"{ref['title']}: page changed since its standard position "
                            f"was validated - positions not applied")
        try:
            data, name = reference_docs.fetch_pdf(ref["url"]), f"{ref['slug']}.pdf"
        except Exception:
            data, name = html, f"{ref['slug']}.html"
        add_artifact(doc, name, data)
        for iid in ref["items"]:
            resp = doc["checklist"].get(iid)
            if not resp or resp["status"] != "unreviewed":
                continue
            pos = ref.get("positions", {}).get(iid)
            if pos and not resp["notes"]:
                if changed:
                    resp["status"] = "needs_info"
                    resp["notes"] = f"[auto] {reference_docs.CHANGED_NOTE}"
                else:
                    resp["status"] = pos["status"]
                    resp["notes"] = f"[auto] {pos['note']}"
            if not resp["evidence"]:
                resp["evidence"] = f"{name} ({ref['url']})"
    doc["updated_at"] = now()
    cosmos.save_review(doc)
    suffix = f"?error={'; '.join(problems)}" if problems else ""
    return RedirectResponse(f"/reviews/{review_id}{suffix}", status_code=303)


@app.post("/reviews/{review_id}/artifacts")
async def upload_artifact(review_id: str, file: UploadFile):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    data = await file.read()
    add_artifact(doc, file.filename, data)
    doc["updated_at"] = now()
    cosmos.save_review(doc)
    return RedirectResponse(f"/reviews/{review_id}", status_code=303)


@app.get("/reviews/{review_id}/artifacts/{name}")
def download_artifact(review_id: str, name: str):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    artifact = next((a for a in doc["artifacts"] if a["name"] == name), None)
    if not artifact:
        return Response(status_code=404)
    data = blob.download_artifact(artifact["blob_path"])
    is_pdf = name.lower().endswith(".pdf")
    return Response(
        content=data,
        media_type="application/pdf" if is_pdf else "application/octet-stream",
        headers={"Content-Disposition":
                 f'{"inline" if is_pdf else "attachment"}; filename="{name}"'},
    )


@app.post("/reviews/{review_id}/decision")
def record_decision(
    review_id: str,
    outcome: str = Form(...),
    approver: str = Form(...),
    conditions: str = Form(""),
    re_review_date: str = Form(""),
):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    gaps = blocker_gaps(doc)
    if outcome == "approved" and gaps:
        ids = ", ".join(i["id"] for i in gaps)
        return RedirectResponse(
            f"/reviews/{review_id}?error=Cannot fully approve: blocker items not "
            f"Pass/NA ({ids}). Use 'Approved with conditions' or resolve them.",
            status_code=303,
        )
    doc["decision"] = {
        "outcome": outcome,
        "approver": approver,
        "conditions": conditions,
        "re_review_date": re_review_date,
        "decided_at": now(),
    }
    doc["status"] = outcome
    doc["updated_at"] = now()
    cosmos.save_review(doc)
    return RedirectResponse(f"/reviews/{review_id}", status_code=303)


@app.get("/reviews/{review_id}/export")
def export_review(review_id: str):
    doc = cosmos.get_review(review_id)
    if not doc:
        return Response(status_code=404)
    doc.pop("_rid", None), doc.pop("_self", None), doc.pop("_etag", None)
    doc.pop("_attachments", None), doc.pop("_ts", None)
    return JSONResponse(
        doc,
        headers={"Content-Disposition": f'attachment; filename="review-{review_id}.json"'},
    )
