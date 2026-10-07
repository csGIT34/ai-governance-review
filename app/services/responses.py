"""Saving answers (optimistic locking) and the preparer -> reviewer sign-off."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, auth
from app.models import Assessment, AssessmentItem, AssessmentScopeNode, Comment, Response, User, utcnow

# Which response fields each assessment kind uses.
FIELDS = {
    "checklist": ("status", "narrative"),
    "controls": ("status", "narrative", "design_rating", "operating_rating",
                 "test_procedure", "test_result", "exceptions"),
}
STATUS_CHOICES = {
    "checklist": {"unreviewed": "— Unreviewed", "pass": "✔ Pass", "fail": "✘ Fail",
                  "na": "N/A", "needs_info": "? Needs info"},
    "controls": {"applicable": "Applicable", "na": "Not applicable here"},
}
DEFAULT_STATUS = {"checklist": "unreviewed", "controls": "applicable"}
RATINGS = {"": "— Not rated", "effective": "Effective",
           "partially_effective": "Partially effective", "ineffective": "Ineffective",
           "not_tested": "Not tested"}
REVIEW_STATES = {"draft": "Draft", "prepared": "Prepared", "reviewed": "Reviewed",
                 "returned": "Returned"}

# action: (allowed from-states, to-state, minimum role)
TRANSITIONS = {
    "prepare": ({"draft", "returned"}, "prepared", "preparer"),
    "review": ({"prepared"}, "reviewed", "reviewer"),
    "return": ({"prepared"}, "returned", "reviewer"),
    "reopen": ({"reviewed"}, "draft", "reviewer"),
}


class SaveError(Exception):
    status_code = 400

    def __init__(self, message: str, response: Response | None = None):
        super().__init__(message)
        self.message = message
        self.response = response


class Forbidden(SaveError):
    status_code = 403


class Conflict(SaveError):
    status_code = 409


class Locked(SaveError):
    status_code = 423


def lock_reason(assessment: Assessment, response: Response | None) -> str:
    """Why this response can't be edited right now ('' if it can)."""
    if assessment.kind == "controls" and assessment.status == "closed":
        return "The assessment is closed."
    if response is not None and response.review_state == "reviewed":
        return "Signed off by the reviewer - reopen it to edit."
    return ""


def missing_fields(kind: str, r: Response) -> list[str]:
    """What still has to be filled in before a response can be marked prepared."""
    if kind == "checklist":
        return ["status"] if r.status in ("", "unreviewed") else []
    if r.status == "na":
        return [] if r.narrative.strip() else ["narrative (why not applicable)"]
    out = [f for f in ("narrative", "design_rating", "operating_rating") if not getattr(r, f).strip()]
    if r.operating_rating not in ("", "not_tested"):
        out += [f for f in ("test_procedure", "test_result") if not getattr(r, f).strip()]
    return out


def find(db: Session, item_id: int, node_id: int) -> Response | None:
    return db.scalar(select(Response).where(Response.assessment_item_id == item_id,
                                            Response.scope_node_id == node_id))


def _clean(kind: str, values: dict) -> dict:
    out = {}
    for field in FIELDS[kind]:
        if field in values:
            out[field] = str(values[field]).replace("\r\n", "\n")
    if "status" in out and out["status"] not in STATUS_CHOICES[kind]:
        raise SaveError(f"Unknown status {out['status']!r}")
    for field in ("design_rating", "operating_rating"):
        if field in out and out[field] not in RATINGS:
            raise SaveError(f"Unknown rating {out[field]!r}")
    return out


def save(db: Session, user: User, assessment: Assessment, item: AssessmentItem,
         node: AssessmentScopeNode, values: dict, expected_version: int,
         action: str = "update", note: str = "") -> Response:
    """Create or update the response for (item, node).

    expected_version is the version the editor loaded (0 = no response existed).
    A mismatch means someone else saved in between -> Conflict, nothing written.
    """
    if not auth.can(user, "preparer"):
        raise Forbidden("Viewers can't edit responses.")
    if item.assessment_id != assessment.id or node.assessment_id != assessment.id:
        raise SaveError("Item or scope node does not belong to this assessment.")
    clean = _clean(assessment.kind, values)
    resp = find(db, item.id, node.id)
    if resp is None:
        if expected_version != 0:
            raise Conflict("This response changed while you were editing.")
        if reason := lock_reason(assessment, None):
            raise Locked(reason)
        resp = Response(assessment_id=assessment.id, assessment_item_id=item.id,
                        scope_node_id=node.id, updated_by=user.upn,
                        **({"status": DEFAULT_STATUS[assessment.kind]} | clean))
        audit.create(db, user.upn, resp, note=note or (action if action != "update" else ""))
        return resp
    if resp.version != expected_version:
        raise Conflict("Someone else saved this response while you were editing.", resp)
    if reason := lock_reason(assessment, resp):
        raise Locked(reason, resp)
    if audit.apply_changes(db, user.upn, resp, clean, action=action, note=note):
        resp.updated_by = user.upn
        if resp.review_state == "prepared":
            audit.apply_changes(db, user.upn, resp, {"review_state": "draft"}, action="state",
                                note="edited after being marked prepared")
        db.flush()  # bumps the version
    return resp


def for_edit(db: Session, user: User, assessment: Assessment, item: AssessmentItem,
             node: AssessmentScopeNode) -> Response:
    """The response for (item, node), created empty if needed, about to have its
    evidence changed. Same lock rules as save(); a prepared response drops back to draft."""
    resp = find(db, item.id, node.id)
    if resp is None:
        return save(db, user, assessment, item, node, {}, 0)
    if not auth.can(user, "preparer"):
        raise Forbidden("Viewers can't edit responses.")
    if reason := lock_reason(assessment, resp):
        raise Locked(reason, resp)
    if resp.review_state == "prepared":
        audit.apply_changes(db, user.upn, resp, {"review_state": "draft"}, action="state",
                            note="evidence changed after being marked prepared")
    return resp


def transition(db: Session, user: User, assessment: Assessment, resp: Response, action: str,
               comment: str = "") -> Response:
    if action not in TRANSITIONS:
        raise SaveError(f"Unknown action {action!r}")
    from_states, to_state, role = TRANSITIONS[action]
    if assessment.kind == "controls" and assessment.status == "closed":
        raise Locked("The assessment is closed.")
    if not auth.can(user, role):
        raise Forbidden(f"'{action}' requires the {role} role.")
    if resp.review_state not in from_states:
        raise SaveError(f"Can't {action} a response that is {resp.review_state}.")
    if action == "review" and resp.prepared_by == user.upn:
        raise Forbidden("Segregation of duties: you prepared this response, so another "
                        "reviewer has to sign it off.")
    if action in ("return", "reopen") and not comment.strip():
        raise SaveError(f"A comment is required to {action} a response.")
    if action == "prepare" and (missing := missing_fields(assessment.kind, resp)):
        raise SaveError("Fill in before marking prepared: " + ", ".join(missing))

    values = {"review_state": to_state}
    if action == "prepare":
        values |= {"prepared_by": user.upn, "prepared_at": utcnow()}
    elif action == "review":
        values |= {"reviewed_by": user.upn, "reviewed_at": utcnow()}
    elif action == "reopen":
        values |= {"reviewed_by": "", "reviewed_at": None}
    audit.apply_changes(db, user.upn, resp, values, action=action, note=comment)
    if comment.strip():
        db.add(Comment(assessment_id=assessment.id, response_id=resp.id,
                       kind=action if action in ("return", "reopen") else "comment",
                       body=comment.strip(), author=user.upn))
    db.flush()
    return resp
