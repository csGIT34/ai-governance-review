"""Who does what: control assignment, the personal work queue, bulk sign-off."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import audit, auth
from app.auth import current_user, require
from app.db import get_session
from app.models import Assessment, AssessmentItem, Issue, Response, User
from app.routes.common import load_assessment
from app.services import assessments, responses
from app.web import redirect, render

router = APIRouter()


def _open_controls():
    return select(Assessment.id).where(Assessment.kind == "controls", Assessment.status == "open")


@router.get("/my")
def my_work(request: Request, user: User = Depends(current_user), db: Session = Depends(get_session)):
    me = user.upn
    # Controls assigned to me that still have unanswered / incomplete subscriptions.
    to_answer = []
    mine = db.scalars(select(AssessmentItem).where(
        AssessmentItem.assignee == me, AssessmentItem.assessment_id.in_(_open_controls()))).all()
    rollups: dict[int, dict] = {}
    for item in mine:
        if item.assessment_id not in rollups:
            a = db.get(Assessment, item.assessment_id)
            rollups[a.id] = {"a": a, "rows": {r["item"].id: r for r in assessments.rollup(db, a)}}
        row = rollups[item.assessment_id]["rows"][item.id]
        todo = row["outcomes"].get("unanswered", 0) + row["outcomes"].get("in_progress", 0)
        if todo:
            to_answer.append({"a": rollups[item.assessment_id]["a"], "item": item, "todo": todo,
                              "leaves": row["leaves"]})

    open_responses = select(Response).where(Response.assessment_id.in_(_open_controls()))
    returned = db.scalars(open_responses.join(AssessmentItem).where(
        Response.review_state == "returned",
        (Response.prepared_by == me) | (AssessmentItem.assignee == me))).all()
    drafts = db.scalars(open_responses.where(Response.review_state == "draft",
                                             Response.updated_by == me)
                        .order_by(Response.updated_at.desc()).limit(50)).all()
    to_review = []
    if auth.can(user, "reviewer"):
        to_review = db.scalars(open_responses.where(Response.review_state == "prepared",
                                                    Response.prepared_by != me)
                               .order_by(Response.prepared_at).limit(200)).all()
    issues = db.scalars(select(Issue).where(
        Issue.status != "closed",
        func.lower(Issue.owner).in_([me, (user.display_name or "").lower()]))
        .order_by(Issue.due_date.is_(None), Issue.due_date)).all()
    names = {a.id: a.name for a in db.scalars(select(Assessment))}
    return render(request, "work/my.html", {
        "to_answer": to_answer, "returned": returned, "drafts": drafts, "to_review": to_review,
        "issues": issues, "names": names})


@router.post("/assessments/{aid}/assign")
async def assign(aid: int, request: Request, user: User = Depends(require("reviewer")),
                 db: Session = Depends(get_session)):
    """Assign one control (item_id) or a whole category to a preparer ('' unassigns)."""
    a = load_assessment(db, aid)
    form = await request.form()
    assignee = str(form.get("assignee", "")).strip().lower()
    if assignee and not db.scalar(select(User.id).where(User.upn == assignee, User.active)):
        return redirect(f"/assessments/{aid}", error=f"{assignee} is not an active user.")
    if form.get("item_id"):
        items = [db.get(AssessmentItem, int(form["item_id"]))]
    else:
        category = str(form.get("category", ""))
        items = [i for i in a.items if not category or i.category == category]
    if not items or any(i is None or i.assessment_id != aid for i in items):
        raise HTTPException(404)
    changed = sum(1 for i in items if audit.apply_changes(db, user.upn, i, {"assignee": assignee}))
    db.commit()
    return redirect(f"/assessments/{aid}", msg=f"Assigned {changed} control(s)."
                    if assignee else f"Unassigned {changed} control(s).")


@router.get("/assessments/{aid}/review")
def review_queue(aid: int, request: Request, user: User = Depends(require("reviewer")),
                 db: Session = Depends(get_session)):
    a = load_assessment(db, aid)
    tree = assessments.Tree(a.scope_nodes)
    pending = db.scalars(select(Response).where(Response.assessment_id == aid,
                                                Response.review_state == "prepared")
                         .order_by(Response.assessment_item_id, Response.id)).all()
    return render(request, "work/review.html", {"a": a, "pending": pending, "tree": tree,
                                                "ratings": responses.RATINGS})


@router.post("/assessments/{aid}/review")
async def bulk_review(aid: int, request: Request, user: User = Depends(require("reviewer")),
                      db: Session = Depends(get_session)):
    """Sign off several prepared answers. Each carries the version the reviewer saw; answers
    that changed since, or that this reviewer prepared/edited, are skipped and reported."""
    a = load_assessment(db, aid)
    form = await request.form()
    comment = str(form.get("comment", ""))
    done, skipped = 0, []
    for pick in form.getlist("pick"):
        try:
            rid, version = (int(x) for x in str(pick).split(":"))
        except ValueError:
            continue
        r = db.get(Response, rid)
        if r is None or r.assessment_id != aid:
            continue
        try:  # transition() validates everything before writing, so a skip writes nothing
            responses.transition(db, user, a, r, "review", version, comment)
            done += 1
        except responses.SaveError as err:
            skipped.append(f"{r.item.ref} at {r.node.name}: {err.message}")
    db.commit()
    return redirect(f"/assessments/{aid}/review", msg=f"Signed off {done} answer(s).",
                    error=("Skipped:\n" + "\n".join(skipped)) if skipped else "")
