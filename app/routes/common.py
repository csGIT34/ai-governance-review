"""Routes shared by every assessment kind: home, response editing and sign-off,
evidence, artifacts, history, users."""
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response as HttpResponse
from sqlalchemy import or_, select
from sqlalchemy.orm.exc import StaleDataError
from starlette.datastructures import UploadFile
from sqlalchemy.orm import Session

from app import audit, auth, blob, config, notify
from app.auth import current_user, require
from app.db import get_session
from app.models import (Assessment, AssessmentItem, AssessmentScopeNode, Artifact, AuditEvent,
                        Comment, EvidenceLink, Issue, Response, User)
from app.services import assessments, evidence, responses
from app.web import redirect, render

router = APIRouter()


def load_assessment(db: Session, aid: int) -> Assessment:
    a = db.get(Assessment, aid)
    if a is None:
        raise HTTPException(404, "Assessment not found")
    return a


def item_and_node(db: Session, a: Assessment, form) -> tuple[AssessmentItem, AssessmentScopeNode]:
    try:
        item = db.get(AssessmentItem, int(form.get("item_id", 0)))
        node = db.get(AssessmentScopeNode, int(form.get("node_id", 0)))
    except ValueError:
        item = node = None
    if not item or not node or item.assessment_id != a.id or node.assessment_id != a.id:
        raise HTTPException(404, "Item or scope node not found in this assessment")
    return item, node


def local_path(target: str, default: str) -> str:
    """Only same-site paths: blocks open redirects like //evil.example."""
    target = str(target or "")
    return target if target.startswith("/") and not target.startswith(("//", "/\\")) else default


def back_to(form, a: Assessment) -> str:
    return local_path(form.get("return_to", ""), f"/assessments/{a.id}")


def anchor(item: AssessmentItem) -> str:
    return f"item-{item.ref}"


@router.get("/")
def home(request: Request, user: User = Depends(current_user), db: Session = Depends(get_session)):
    rows = db.scalars(select(Assessment).order_by(Assessment.created_at.desc())).all()
    open_issues = db.scalar(select(Issue.id).where(Issue.status != "closed").limit(1))
    return render(request, "index.html", {
        "assessments": [(a, assessments.progress(db, a)) for a in rows],
        "has_open_issues": open_issues is not None,
    })


@router.post("/dev/login")
def dev_login(upn: str = Form(...), return_to: str = Form("/")):
    if config.AUTH_MODE != "dev":
        raise HTTPException(404)
    resp = RedirectResponse(local_path(return_to, "/"), status_code=303)
    resp.set_cookie("dev_user", upn, httponly=True, samesite="lax")
    return resp


# --- responses -----------------------------------------------------------------------

def _error_json(a: Assessment, err: responses.SaveError) -> JSONResponse:
    body = {"message": err.message}
    if err.response is not None:
        r = err.response
        body["current"] = {f: getattr(r, f) for f in responses.FIELDS[a.kind]}
        body["current"] |= {"version": r.version, "updated_by": r.updated_by,
                            "review_state": r.review_state}
    return JSONResponse(body, status_code=err.status_code)


@router.post("/assessments/{aid}/responses")
async def save_response(aid: int, request: Request, user: User = Depends(current_user),
                        db: Session = Depends(get_session)):
    """Autosave endpoint: fields of one response + the version the editor loaded."""
    form = await request.form()
    a = load_assessment(db, aid)
    item, node = item_and_node(db, a, form)
    try:
        version = int(form.get("version", 0))
    except ValueError:
        version = -1
    try:
        resp = responses.save(db, user, a, item, node, dict(form), version)
        db.commit()
    except responses.SaveError as err:
        out = _error_json(a, err)
        db.rollback()
        return out
    except StaleDataError:  # a concurrent save won the race between check and write
        db.rollback()
        return JSONResponse({"message": "Someone else saved this response at the same moment."},
                            status_code=409)
    return {"version": resp.version, "review_state": resp.review_state,
            "response_id": resp.id}


@router.post("/assessments/{aid}/responses/transition")
async def transition(aid: int, request: Request, background: BackgroundTasks,
                     user: User = Depends(current_user), db: Session = Depends(get_session)):
    form = await request.form()
    a = load_assessment(db, aid)
    item, node = item_and_node(db, a, form)
    back = back_to(form, a)
    resp = responses.find(db, item.id, node.id)
    if resp is None:
        return redirect(back, error="Nothing has been answered here yet.", anchor=anchor(item))
    action = str(form.get("action", ""))
    try:
        version = int(form.get("version", -1))
    except ValueError:
        version = -1
    try:
        responses.transition(db, user, a, resp, action, version, str(form.get("comment", "")))
    except responses.SaveError as err:
        db.rollback()
        return redirect(back, error=f"{item.ref}: {err.message}", anchor=anchor(item))
    db.commit()
    where = f"/assessments/{aid}/items/{item.id}?node={node.id}"
    if action == "prepare":
        background.add_task(notify.send, f"Ready for review: {item.ref} at {node.name}",
                            item.title, [("Assessment", a.name), ("Prepared by", user.upn)], where)
    elif action == "return":
        background.add_task(notify.send, f"Returned to {resp.prepared_by}: {item.ref} at {node.name}",
                            str(form.get("comment", "")), [("Assessment", a.name),
                                                           ("Returned by", user.upn)], where)
    done = {"prepare": "marked prepared", "review": "signed off", "return": "returned to preparer",
            "reopen": "reopened"}[action]
    return redirect(back, msg=f"{item.ref} {done}.", anchor=anchor(item))


@router.post("/assessments/{aid}/responses/comment")
async def comment(aid: int, request: Request, user: User = Depends(require("preparer")),
                  db: Session = Depends(get_session)):
    form = await request.form()
    a = load_assessment(db, aid)
    item, node = item_and_node(db, a, form)
    back = back_to(form, a)
    if reason := responses.lock_reason(a, None):
        return redirect(back, error=reason)
    body = str(form.get("body", "")).strip()
    resp = responses.find(db, item.id, node.id)
    if resp is None:  # comments belong to an answer; don't create an empty override
        return redirect(back, error="Comments go on an answer given at this level.",
                        anchor=anchor(item))
    if body:
        audit.create(db, user.upn, Comment(assessment_id=a.id, response_id=resp.id, body=body,
                                           author=user.upn))
        db.commit()
    return redirect(back, anchor=anchor(item))


@router.get("/responses/{rid}/history")
def response_history(rid: int, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_session)):
    resp = db.get(Response, rid)
    if resp is None:
        raise HTTPException(404)
    link_ids = [str(i) for i in db.scalars(select(EvidenceLink.id).where(EvidenceLink.response_id == rid))]
    events = db.scalars(select(AuditEvent).where(or_(
        (AuditEvent.entity_type == "responses") & (AuditEvent.entity_id == str(rid)),
        (AuditEvent.entity_type == "evidence_links") & AuditEvent.entity_id.in_(link_ids),
    )).order_by(AuditEvent.id.desc())).all()
    comments = db.scalars(select(Comment).where(Comment.response_id == rid)
                          .order_by(Comment.id.desc())).all()
    return render(request, "_history.html", {"events": events, "comments": comments})


# --- evidence --------------------------------------------------------------------------

@router.post("/assessments/{aid}/responses/evidence")
async def add_evidence(aid: int, request: Request, user: User = Depends(current_user),
                       db: Session = Depends(get_session)):
    form = await request.form()
    a = load_assessment(db, aid)
    item, node = item_and_node(db, a, form)
    back = back_to(form, a)
    try:
        resp = responses.for_edit(db, user, a, item, node)
        link = evidence.add_link(db, user, a, str(form.get("url", "")), str(form.get("title", "")),
                                 response=resp)
    except (responses.SaveError, ValueError) as err:
        db.rollback()
        return redirect(back, error=f"{item.ref}: {getattr(err, 'message', err)}", anchor=anchor(item))
    db.commit()
    if link.pinned is False:
        return redirect(back, anchor=anchor(item), error=f"{item.ref}: " + (
            link.message or "that repo link points at a branch, not a commit - it will change as "
            "the branch moves. Use a permalink (press 'y' on the GitHub file page)."))
    return redirect(back, msg=f"{item.ref}: {link.message}" if link.message else "",
                    anchor=anchor(item))


@router.post("/evidence/{lid}/remove")
async def remove_evidence(lid: int, request: Request, user: User = Depends(current_user),
                          db: Session = Depends(get_session)):
    form = await request.form()
    link = db.get(EvidenceLink, lid)
    if link is None or link.removed_at:
        raise HTTPException(404)
    a = load_assessment(db, link.assessment_id)
    back = back_to(form, a)
    try:
        if link.response_id:
            resp = db.get(Response, link.response_id)
            responses.for_edit(db, user, a, resp.item, resp.node)
        elif not auth.can(user, "preparer"):
            raise responses.Forbidden("Viewers can't remove evidence.")
        evidence.remove_link(db, user, link)
    except responses.SaveError as err:
        db.rollback()
        return redirect(back, error=err.message)
    db.commit()
    return redirect(back, msg="Evidence link removed (kept in the history).")


# --- artifacts -------------------------------------------------------------------------

@router.post("/assessments/{aid}/artifacts")
async def upload_artifact(aid: int, request: Request, user: User = Depends(require("preparer")),
                          db: Session = Depends(get_session)):
    form = await request.form()
    a = load_assessment(db, aid)
    back = back_to(form, a)
    if reason := responses.lock_reason(a, None):
        return redirect(back, error=reason)
    upload = form.get("file")
    if not isinstance(upload, UploadFile) or not upload.filename:
        return redirect(back, error="Choose a file to upload.")
    if (upload.size or 0) > config.MAX_UPLOAD_MB * 1024 * 1024:  # before reading it into memory
        return redirect(back, error=f"File is larger than {config.MAX_UPLOAD_MB} MB.")
    try:
        resp = None
        if form.get("item_id"):
            item, node = item_and_node(db, a, form)
            resp = responses.for_edit(db, user, a, item, node)
        art = evidence.store_artifact(db, user, a, upload.filename, await upload.read(),
                                      upload.content_type or "")
        if resp is not None:
            evidence.add_link(db, user, a, "", art.filename, response=resp, artifact=art)
    except (responses.SaveError, ValueError) as err:
        db.rollback()
        return redirect(back, error=str(getattr(err, "message", err)))
    db.commit()
    return redirect(back, msg=f"Uploaded {art.filename}.",
                    anchor=anchor(resp.item) if resp is not None else "")


INLINE_TYPES = ("application/pdf", "image/png", "image/jpeg", "image/gif", "text/plain")


@router.get("/artifacts/{art_id}")
def download_artifact(art_id: int, user: User = Depends(current_user),
                      db: Session = Depends(get_session)):
    art = db.get(Artifact, art_id)
    if art is None:
        raise HTTPException(404)
    inline = art.content_type in INLINE_TYPES  # never inline HTML/SVG: it would run on our origin
    return HttpResponse(
        content=blob.download(art.blob_path),
        media_type=art.content_type if inline else "application/octet-stream",
        headers={"Content-Disposition":
                 f'{"inline" if inline else "attachment"}; filename="{art.filename}"',
                 "X-Content-Type-Options": "nosniff"})


# --- activity & users ------------------------------------------------------------------

@router.get("/assessments/{aid}/activity")
def activity(aid: int, request: Request, user: User = Depends(current_user),
             db: Session = Depends(get_session)):
    a = load_assessment(db, aid)
    events = db.scalars(select(AuditEvent).where(AuditEvent.assessment_id == aid)
                        .order_by(AuditEvent.id.desc()).limit(2000)).all()
    return render(request, "activity.html", {"a": a, "events": events})


@router.get("/admin/users")
def users_page(request: Request, user: User = Depends(require("admin")),
               db: Session = Depends(get_session)):
    users = db.scalars(select(User).order_by(User.upn)).all()
    return render(request, "users.html", {"users": users, "roles": auth.ROLES,
                                          "role_help": auth.ROLE_HELP})


@router.post("/admin/users/{uid}")
def update_user(uid: int, role: str = Form(...), active: str = Form(""),
                user: User = Depends(require("admin")), db: Session = Depends(get_session)):
    target = db.get(User, uid)
    if target is None or role not in auth.ROLES:
        raise HTTPException(404)
    values = {"role": role, "active": active == "on"}
    if target.role == "admin" and (role != "admin" or not values["active"]):
        admins = db.scalars(select(User).where(User.role == "admin", User.active)).all()
        if len(admins) <= 1:
            return redirect("/admin/users", error="Can't remove the last active admin.")
    audit.apply_changes(db, user.upn, target, values)
    db.commit()
    return redirect("/admin/users", msg=f"Updated {target.upn}.")
