"""AI-model enablement reviews (checklist kind) and their reference-doc settings."""
import copy
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, checklist
from app.auth import current_user, require
from app.db import get_session
from app.enrichment import azure_catalog, gcp_catalog, reference_docs
from app.models import (Artifact, AuditEvent, Comment, EvidenceLink, Library, Response, Setting,
                        User)
from app.routes.common import load_assessment
from app.services import ai_review, assessments, responses
from app.web import redirect, render

router = APIRouter()


def _checklist(db: Session, aid: int):
    a = load_assessment(db, aid)
    if a.kind != "checklist":
        raise HTTPException(404, "Not an AI enablement review")
    return a


@router.get("/assessments/new/ai")
def new_form(request: Request, user: User = Depends(require("preparer"))):
    return render(request, "ai/new.html", {"gcp_publishers": gcp_catalog.PUBLISHERS})


@router.post("/assessments/ai")
def create(csp: str = Form(...), service_name: str = Form(...), model_name: str = Form(...),
           model_version: str = Form(""), publisher: str = Form(""), regions: str = Form(""),
           requested_by: str = Form(""), justification: str = Form(""),
           user: User = Depends(require("preparer")), db: Session = Depends(get_session)):
    if csp not in ("azure", "gcp"):
        raise HTTPException(400, "csp must be azure or gcp")
    if csp == "gcp" and publisher not in gcp_catalog.PUBLISHERS:
        raise HTTPException(400, f"publisher must be one of {', '.join(gcp_catalog.PUBLISHERS)}")
    library = db.scalar(select(Library).where(Library.key == ai_review.AI_LIBRARY_KEY))
    name = f"{service_name} — {model_name}" + (f" ({model_version})" if model_version else "")
    a = assessments.create_checklist(db, user, library, name, {
        "csp": csp, "service_name": service_name, "model_name": model_name.strip(),
        "model_version": model_version.strip(), "publisher": publisher, "regions": regions,
        "requested_by": requested_by, "justification": justification})
    db.commit()
    return redirect(f"/assessments/{a.id}")


def source_groups(a, resp_by_item: dict) -> list[dict]:
    """Checklist items grouped by where the answer comes from, with open counts."""
    groups = []
    for src in checklist.SOURCES:
        items = [i for i in a.items if i.extra.get("source") == src["id"]]
        open_refs = [i.ref for i in items
                     if not resp_by_item.get(i.id) or resp_by_item[i.id].status == "unreviewed"]
        groups.append({**src, "items": items, "open_refs": open_refs})
    known = {s["id"] for s in checklist.SOURCES}
    other = [i for i in a.items if i.extra.get("source") not in known]
    if other:  # items added to the library in-app without a source
        groups.append({"id": "other", "label": "Other items", "hint": "", "items": other,
                       "open_refs": [i.ref for i in other if not resp_by_item.get(i.id)]})
    return groups


def review_page(request: Request, db: Session, user: User, a):
    node = ai_review.node(a)
    resp_by_item = {r.assessment_item_id: r for r in db.scalars(
        select(Response).where(Response.scope_node_id == node.id))}
    docs = reference_docs.get_docs(db)
    artifacts = db.scalars(select(Artifact).where(Artifact.assessment_id == a.id)
                           .order_by(Artifact.id)).all()
    decisions = db.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == "assessments", AuditEvent.entity_id == str(a.id),
        AuditEvent.action == "decision").order_by(AuditEvent.id.desc())).all()
    return render(request, "ai/review.html", {
        "a": a, "ctx": a.context, "node": node, "resp_by_item": resp_by_item,
        "groups": source_groups(a, resp_by_item),
        "blocker_gaps": ai_review.blocker_gaps(db, a),
        "progress": assessments.progress(db, a),
        "ref_docs": docs.get(a.context["csp"], []), "internal_docs": docs.get("internal", []),
        "artifacts": artifacts, "past_decisions": decisions[1:],
        "status_choices": responses.STATUS_CHOICES["checklist"],
    })


@router.post("/assessments/{aid}/enrich")
def enrich(aid: int, user: User = Depends(require("preparer")), db: Session = Depends(get_session)):
    a = _checklist(db, aid)
    snapshot = ai_review.enrich(db, user, a)
    db.commit()
    return redirect(f"/assessments/{aid}", error=snapshot.get("error", ""))


@router.post("/assessments/{aid}/reference-docs")
def fetch_reference_docs(aid: int, user: User = Depends(require("preparer")),
                         db: Session = Depends(get_session)):
    a = _checklist(db, aid)
    problems = ai_review.fetch_reference_docs(db, user, a)
    db.commit()
    return redirect(f"/assessments/{aid}", error="; ".join(problems),
                    msg="" if problems else "Reference docs saved and linked.")


@router.post("/assessments/{aid}/decision")
def record_decision(aid: int, outcome: str = Form(...), conditions: str = Form(""),
                    re_review_date: str = Form(""), user: User = Depends(require("reviewer")),
                    db: Session = Depends(get_session)):
    a = _checklist(db, aid)
    if outcome not in ("approved", "conditional", "rejected"):
        raise HTTPException(400, "Unknown outcome")
    gaps = ai_review.blocker_gaps(db, a)
    if outcome == "approved" and gaps:
        return redirect(f"/assessments/{aid}", anchor="decision", error=(
            "Cannot fully approve: blocker items not Pass/N-A ("
            + ", ".join(i.ref for i in gaps) + "). Use 'Approved with conditions' or resolve them."))
    if outcome != "approved" and not conditions.strip():
        return redirect(f"/assessments/{aid}", anchor="decision",
                        error="Conditions / rationale are required for this outcome.")
    decision = {"outcome": outcome, "approver": user.upn, "approver_name": user.display_name,
                "conditions": conditions.strip(), "re_review_date": re_review_date,
                "decided_at": datetime.now(timezone.utc).isoformat()}
    audit.apply_changes(db, user.upn, a, {"decision": decision, "status": outcome},
                        action="decision", note=conditions.strip())
    db.commit()
    return redirect(f"/assessments/{aid}", msg="Decision recorded.", anchor="decision")


@router.get("/assessments/{aid}/export.json")
def export_json(aid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    a = _checklist(db, aid)
    node = ai_review.node(a)
    out_items = []
    for item in a.items:
        r = responses.find(db, item.id, node.id)
        links = db.scalars(select(EvidenceLink).where(
            EvidenceLink.response_id == r.id, EvidenceLink.removed_at.is_(None))).all() if r else []
        out_items.append({
            "ref": item.ref, "title": item.title, "category": item.category,
            "severity": item.severity,
            "status": r.status if r else "unreviewed", "notes": r.narrative if r else "",
            "updated_by": r.updated_by if r else "",
            "updated_at": r.updated_at.isoformat() if r else None,
            "evidence": [{"title": l.title, "url": l.url, "artifact_id": l.artifact_id}
                         for l in links],
            "comments": [c.body for c in db.scalars(select(Comment).where(
                Comment.response_id == r.id))] if r else [],
        })
    artifacts = db.scalars(select(Artifact).where(Artifact.assessment_id == aid)).all()
    return JSONResponse({
        "id": a.id, "name": a.name, "status": a.status, "context": a.context,
        "library": a.library_snapshot, "created_by": a.created_by,
        "created_at": a.created_at.isoformat(), "enrichment": a.enrichment,
        "decision": a.decision, "items": out_items,
        "artifacts": [{"id": x.id, "filename": x.filename, "sha256": x.sha256, "size": x.size,
                       "uploaded_by": x.uploaded_by, "uploaded_at": x.uploaded_at.isoformat()}
                      for x in artifacts],
    }, headers={"Content-Disposition": f'attachment; filename="review-{aid}.json"'})


@router.get("/api/catalog-models")
def catalog_models(csp: str, region: str = "", publisher: str = "google",
                   user: User = Depends(current_user)):
    """Model ids for the new-review form pickers, from the live CSP catalog."""
    if csp == "azure":
        first_region = (region or "eastus").split(",")[0].strip() or "eastus"
        return JSONResponse(azure_catalog.list_models(first_region))
    return JSONResponse(gcp_catalog.list_models(publisher))


# --- reference-doc settings (admin) -------------------------------------------------

@router.get("/api/check-url")
def check_url(url: str, user: User = Depends(require("admin"))):
    """Settings page 'Check' button: verify a reference-doc URL loads, using the same
    (SSRF-guarded) fetch the snapshotter uses."""
    try:
        reference_docs.fetch(url)
        return {"ok": True, "detail": "page loads"}
    except Exception as err:
        return {"ok": False, "detail": str(err)}


@router.get("/settings")
def settings_page(request: Request, user: User = Depends(require("admin")),
                  db: Session = Depends(get_session)):
    return render(request, "settings.html", {
        "docs": reference_docs.get_docs(db),
        "customized": db.get(Setting, reference_docs.SETTING_KEY) is not None,
    })


def _save_ref_docs(db: Session, user: User, docs: dict):
    current = db.get(Setting, reference_docs.SETTING_KEY)
    if current is None:
        audit.create(db, user.upn, Setting(key=reference_docs.SETTING_KEY, value={"docs": docs},
                                           updated_by=user.upn))
    else:
        audit.apply_changes(db, user.upn, current, {"value": {"docs": docs}, "updated_by": user.upn})
    db.commit()


def _parse_item_ids(raw: str) -> tuple[list[str], list[str]]:
    """Comma-separated checklist ids -> (ids, unknown ids)."""
    ids = [s.strip().upper() for s in raw.split(",") if s.strip()]
    known = {i["id"] for i in checklist.ITEMS}
    return ids, [i for i in ids if i not in known]


@router.post("/settings/reference-docs/save")
async def save_reference_docs(request: Request, user: User = Depends(require("admin")),
                              db: Session = Depends(get_session)):
    form = await request.form()
    csp = form.get("csp", "")
    docs = copy.deepcopy(reference_docs.get_docs(db))
    for ref in docs.get(csp, []):
        slug = ref["slug"]
        if f"url__{slug}" not in form:
            continue
        url = form.get(f"url__{slug}", "").strip()
        if not url.startswith(("http://", "https://")):
            return redirect("/settings", error=f"{ref['title']}: URL must start with http(s)://")
        ids, unknown = _parse_item_ids(form.get(f"items__{slug}", ""))
        if unknown:
            return redirect("/settings", error=f"{ref['title']}: unknown checklist items "
                                               f"{', '.join(unknown)}")
        ref["url"] = url
        ref["title"] = form.get(f"title__{slug}", "").strip() or ref["title"]
        ref["items"] = ids
    _save_ref_docs(db, user, docs)
    return redirect("/settings", msg="Saved.")


@router.post("/settings/reference-docs/add")
def add_reference_doc(csp: str = Form(...), title: str = Form(...), url: str = Form(...),
                      items: str = Form(""), user: User = Depends(require("admin")),
                      db: Session = Depends(get_session)):
    if not url.strip().startswith(("http://", "https://")):
        return redirect("/settings", error="URL must start with http(s)://")
    ids, unknown = _parse_item_ids(items)
    if unknown:
        return redirect("/settings", error=f"Unknown checklist items: {', '.join(unknown)}")
    docs = copy.deepcopy(reference_docs.get_docs(db))
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60] or "doc"
    existing, base, n = {d["slug"] for d in docs.get(csp, [])}, slug, 2
    while slug in existing:
        slug, n = f"{base}-{n}", n + 1
    docs.setdefault(csp, []).append(
        {"slug": slug, "title": title.strip(), "url": url.strip(), "items": ids})
    _save_ref_docs(db, user, docs)
    return redirect("/settings", msg=f"Added {title.strip()}.")


@router.post("/settings/reference-docs/delete")
async def delete_reference_doc(request: Request, user: User = Depends(require("admin")),
                               db: Session = Depends(get_session)):
    form = await request.form()
    csp, slug = form.get("csp", ""), form.get("delete_slug", "")
    docs = copy.deepcopy(reference_docs.get_docs(db))
    docs[csp] = [d for d in docs.get(csp, []) if d["slug"] != slug]
    _save_ref_docs(db, user, docs)
    return redirect("/settings", msg="Removed.")


@router.post("/settings/reference-docs/reset")
def reset_reference_docs(user: User = Depends(require("admin")), db: Session = Depends(get_session)):
    current = db.get(Setting, reference_docs.SETTING_KEY)
    if current is not None:
        audit.record(db, user.upn, "delete", current, {"value": (current.value, None)},
                     note="reset to bundled defaults")
        db.delete(current)
        db.commit()
    return redirect("/settings", msg="Reset to the bundled defaults.")
