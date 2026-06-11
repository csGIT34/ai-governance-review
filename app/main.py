import logging
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from app import checklist
from app.db import blob, cosmos
from app.enrichment import azure_catalog, gcp_catalog

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    cosmos.ensure_resources()
    yield


app = FastAPI(title="AI Governance Review", lifespan=lifespan)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def index(request: Request):
    reviews = cosmos.list_reviews()
    for r in reviews:
        r["progress"] = progress(r)
    return templates.TemplateResponse(request, "index.html", {"reviews": reviews})


@app.get("/reviews/new")
def new_review_form(request: Request):
    return templates.TemplateResponse(request, "new.html", {})


@app.post("/reviews")
def create_review(
    csp: str = Form(...),
    service_name: str = Form(...),
    model_name: str = Form(...),
    model_version: str = Form(""),
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
    return templates.TemplateResponse(request, "review.html", {
        "r": doc,
        "items": checklist.ITEMS,
        "categories": checklist.CATEGORIES,
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
        snapshot = gcp_catalog.enrich(doc["model_name"])
    doc["enrichment"] = snapshot
    doc["updated_at"] = now()
    cosmos.save_review(doc)
    return RedirectResponse(f"/reviews/{review_id}", status_code=303)


@app.post("/reviews/{review_id}/artifacts")
async def upload_artifact(review_id: str, file: UploadFile):
    doc = cosmos.get_review(review_id)
    if not doc:
        return RedirectResponse("/", status_code=303)
    data = await file.read()
    blob_path = blob.upload_artifact(review_id, file.filename, data)
    doc["artifacts"] = [a for a in doc["artifacts"] if a["name"] != file.filename]
    doc["artifacts"].append({
        "name": file.filename,
        "blob_path": blob_path,
        "size": len(data),
        "uploaded_at": now(),
    })
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
    return Response(
        content=data,
        media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
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
