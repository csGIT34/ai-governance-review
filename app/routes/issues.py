"""Issues: control gaps / exceptions with an owner, due date and action plan.
They outlive the assessment that raised them and stay editable after it closes."""
from datetime import date

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.auth import current_user, require
from app.db import get_session
from app.models import Assessment, AuditEvent, Comment, EvidenceLink, Issue, User, utcnow
from app.routes.common import back_to, item_and_node, load_assessment
from app.services import evidence, responses
from app.web import redirect, render

router = APIRouter()

SEVERITIES = ["low", "medium", "high", "critical"]
STATUSES = {"open": "Open", "in_progress": "In progress", "risk_accepted": "Risk accepted",
            "closed": "Closed"}


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


@router.post("/assessments/{aid}/issues")
async def raise_issue(aid: int, request: Request, user: User = Depends(require("preparer")),
                      db: Session = Depends(get_session)):
    form = await request.form()
    a = load_assessment(db, aid)
    item, node = item_and_node(db, a, form)
    back = back_to(form, a)
    if a.status == "closed":
        return redirect(back, error="The assessment is closed.")
    title = str(form.get("title", "")).strip()
    severity = str(form.get("severity", "medium"))
    if not title or severity not in SEVERITIES:
        return redirect(back, error="An issue needs a title and a valid severity.")
    own = responses.find(db, item.id, node.id)
    issue = audit.create(db, user.upn, Issue(
        assessment_id=aid, response_id=own.id if own else None, assessment_item_id=item.id,
        scope_node_id=node.id, title=title, description=str(form.get("description", "")).strip(),
        severity=severity, owner=str(form.get("owner", "")).strip(),
        due_date=_date(str(form.get("due_date", ""))),
        remediation_plan=str(form.get("remediation_plan", "")).strip(), created_by=user.upn))
    db.commit()
    return redirect(back, msg=f"Raised {issue.ref}.", anchor=f"item-{item.ref}")


def _list(request: Request, db: Session, a: Assessment | None, status: str, severity: str):
    q = select(Issue).order_by(Issue.due_date.is_(None), Issue.due_date, Issue.id)
    if a is not None:
        q = q.where(Issue.assessment_id == a.id)
    if status == "open":
        q = q.where(Issue.status != "closed")
    elif status:
        q = q.where(Issue.status == status)
    if severity:
        q = q.where(Issue.severity == severity)
    issues = db.scalars(q).all()
    names = {x.id: x.name for x in db.scalars(select(Assessment).where(
        Assessment.id.in_({i.assessment_id for i in issues})))}
    return render(request, "issues/list.html", {
        "a": a, "issues": issues, "names": names, "status": status, "severity": severity,
        "statuses": STATUSES, "severities": SEVERITIES, "today": date.today()})


@router.get("/issues")
def all_issues(request: Request, status: str = "open", severity: str = "",
               user: User = Depends(current_user), db: Session = Depends(get_session)):
    return _list(request, db, None, status, severity)


@router.get("/assessments/{aid}/issues")
def assessment_issues(aid: int, request: Request, status: str = "open", severity: str = "",
                      user: User = Depends(current_user), db: Session = Depends(get_session)):
    return _list(request, db, load_assessment(db, aid), status, severity)


def _issue(db: Session, iid: int) -> Issue:
    issue = db.get(Issue, iid)
    if issue is None:
        raise HTTPException(404, "Issue not found")
    return issue


@router.get("/issues/{iid}")
def issue_page(iid: int, request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_session)):
    issue = _issue(db, iid)
    link_ids = [str(i) for i in db.scalars(select(EvidenceLink.id).where(EvidenceLink.issue_id == iid))]
    events = db.scalars(select(AuditEvent).where(
        ((AuditEvent.entity_type == "issues") & (AuditEvent.entity_id == str(iid)))
        | ((AuditEvent.entity_type == "evidence_links") & AuditEvent.entity_id.in_(link_ids)))
        .order_by(AuditEvent.id.desc())).all()
    comments = db.scalars(select(Comment).where(Comment.issue_id == iid).order_by(Comment.id.desc())).all()
    return render(request, "issues/detail.html", {
        "issue": issue, "a": db.get(Assessment, issue.assessment_id), "events": events,
        "comments": comments, "statuses": STATUSES, "severities": SEVERITIES})


@router.post("/issues/{iid}")
def update_issue(iid: int, version: int = Form(...), title: str = Form(...),
                 description: str = Form(""), severity: str = Form(...), status: str = Form(...),
                 owner: str = Form(""), due_date: str = Form(""), remediation_plan: str = Form(""),
                 note: str = Form(""), user: User = Depends(require("preparer")),
                 db: Session = Depends(get_session)):
    issue = _issue(db, iid)
    back = f"/issues/{iid}"
    if issue.version != version:
        return redirect(back, error="Someone else changed this issue while you were editing - "
                                    "reload and re-apply your change.")
    if severity not in SEVERITIES or status not in STATUSES or not title.strip():
        return redirect(back, error="Title, severity and status are required.")
    if status in ("closed", "risk_accepted") and status != issue.status and not note.strip():
        return redirect(back, error=f"Say why when setting the issue to {STATUSES[status].lower()}.")
    values = {"title": title.strip(), "description": description.strip(), "severity": severity,
              "status": status, "owner": owner.strip(), "due_date": _date(due_date),
              "remediation_plan": remediation_plan.strip()}
    if status == "closed" and issue.status != "closed":
        values["closed_at"] = utcnow()
    elif status != "closed":
        values["closed_at"] = None
    if audit.apply_changes(db, user.upn, issue, values, note=note.strip()):
        db.flush()  # bumps the version
    if note.strip():
        db.add(Comment(assessment_id=issue.assessment_id, issue_id=iid, body=note.strip(),
                       author=user.upn))
    db.commit()
    return redirect(back, msg="Saved.")


@router.post("/issues/{iid}/evidence")
def issue_evidence(iid: int, url: str = Form(...), title: str = Form(""),
                   user: User = Depends(require("preparer")), db: Session = Depends(get_session)):
    issue = _issue(db, iid)
    try:
        link = evidence.add_link(db, user, db.get(Assessment, issue.assessment_id), url, title,
                                 issue=issue)
    except ValueError as err:
        db.rollback()
        return redirect(f"/issues/{iid}", error=str(err))
    db.commit()
    return redirect(f"/issues/{iid}", error="" if link.pinned is not False else
                    "That repo link points at a branch, not a commit - use a permalink.")
