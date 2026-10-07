"""Control assessments (RCSA): libraries, Azure scope, assessments, the per-control
editor with management-group inheritance."""
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import Response as HttpResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile

from app import audit
from app.auth import current_user, require
from app.db import get_session
from app.models import (Assessment, AssessmentItem, Comment, EvidenceLink, Issue, Library,
                        LibraryItem, Response, ScopeNode, User)
from app.routes import ai
from app.routes.common import load_assessment
from app.services import assessments, export, libraries, responses, scope
from app.web import redirect, render

router = APIRouter()

OUTCOME_LABELS = {"effective": "Effective", "partially_effective": "Partially effective",
                  "ineffective": "Ineffective", "not_tested": "Not tested", "na": "N/A",
                  "in_progress": "In progress", "unanswered": "Unanswered"}


def _controls(db: Session, aid: int) -> Assessment:
    a = load_assessment(db, aid)
    if a.kind != "controls":
        raise HTTPException(404, "Not a control assessment")
    return a


def _date(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# --- libraries -------------------------------------------------------------------------

@router.get("/libraries")
def libraries_page(request: Request, user: User = Depends(current_user),
                   db: Session = Depends(get_session)):
    rows = db.execute(select(Library, func.count(LibraryItem.id))
                      .outerjoin(LibraryItem, (LibraryItem.library_id == Library.id) & LibraryItem.active)
                      .group_by(Library.id).order_by(Library.kind.desc(), Library.name)).all()
    return render(request, "controls/libraries.html", {"rows": rows})


@router.post("/libraries")
def create_library(name: str = Form(...), source_url: str = Form(""), source_version: str = Form(""),
                   description: str = Form(""), user: User = Depends(require("admin")),
                   db: Session = Depends(get_session)):
    if _bad_url(source_url):
        return redirect("/libraries", error="The document link must start with http:// or https://")
    key, n = libraries.slug(name), 2
    base = key
    while db.scalar(select(Library.id).where(Library.key == key)):
        key, n = f"{base}-{n}", n + 1
    lib = audit.create(db, user.upn, Library(key=key, name=name.strip(), kind="controls",
                                             source_url=source_url.strip(),
                                             source_version=source_version.strip(),
                                             description=description.strip()))
    db.commit()
    return redirect(f"/libraries/{lib.id}", msg="Library created - add controls or import them.")


def _bad_url(url: str) -> bool:
    return bool(url.strip()) and not url.strip().startswith(("http://", "https://"))


def _library(db: Session, lid: int) -> Library:
    lib = db.get(Library, lid)
    if lib is None:
        raise HTTPException(404, "Library not found")
    return lib


@router.get("/libraries/{lid}")
def library_page(lid: int, request: Request, show: str = "active", user: User = Depends(current_user),
                 db: Session = Depends(get_session)):
    lib = _library(db, lid)
    items = [i for i in lib.items if show == "all" or i.active]
    used_by = db.scalars(select(Assessment).where(Assessment.library_id == lid)
                         .order_by(Assessment.created_at.desc())).all()
    return render(request, "controls/library.html", {"lib": lib, "items": items, "show": show,
                                                      "used_by": used_by,
                                                      "columns": libraries.IMPORT_COLUMNS})


@router.post("/libraries/{lid}")
def update_library(lid: int, name: str = Form(...), source_url: str = Form(""),
                   source_version: str = Form(""), description: str = Form(""),
                   user: User = Depends(require("admin")), db: Session = Depends(get_session)):
    lib = _library(db, lid)
    if _bad_url(source_url):
        return redirect(f"/libraries/{lid}", error="The document link must start with http:// or https://")
    audit.apply_changes(db, user.upn, lib, {"name": name.strip(), "source_url": source_url.strip(),
                                            "source_version": source_version.strip(),
                                            "description": description.strip()})
    db.commit()
    return redirect(f"/libraries/{lid}", msg="Saved.")


def _item_values(form) -> dict:
    return {f: str(form.get(f, "")).strip() for f in libraries.EDITABLE if f in form}


@router.post("/libraries/{lid}/items")
async def add_item(lid: int, request: Request, user: User = Depends(require("admin")),
                   db: Session = Depends(get_session)):
    lib = _library(db, lid)
    values = _item_values(await request.form())
    if not values.get("ref") or not values.get("title"):
        return redirect(f"/libraries/{lid}", error="Ref and title are required.")
    if any(i.ref == values["ref"] for i in lib.items):
        return redirect(f"/libraries/{lid}", error=f"{values['ref']} already exists.")
    position = max((i.position for i in lib.items), default=-1) + 1
    audit.create(db, user.upn, LibraryItem(library_id=lid, position=position, **values))
    db.commit()
    return redirect(f"/libraries/{lid}", msg=f"Added {values['ref']}.")


@router.get("/libraries/{lid}/items/{iid}")
def item_form(lid: int, iid: int, request: Request, user: User = Depends(require("admin")),
              db: Session = Depends(get_session)):
    item = db.get(LibraryItem, iid)
    if item is None or item.library_id != lid:
        raise HTTPException(404)
    return render(request, "controls/library_item.html", {"lib": item.library, "item": item})


@router.post("/libraries/{lid}/items/{iid}")
async def update_item(lid: int, iid: int, request: Request, user: User = Depends(require("admin")),
                      db: Session = Depends(get_session)):
    item = db.get(LibraryItem, iid)
    if item is None or item.library_id != lid:
        raise HTTPException(404)
    form = await request.form()
    values = _item_values(form) | {"active": form.get("active") == "on"}
    if not values.get("ref") or not values.get("title"):
        return redirect(f"/libraries/{lid}/items/{iid}", error="Ref and title are required.")
    clash = db.scalar(select(LibraryItem.id).where(LibraryItem.library_id == lid,
                                                   LibraryItem.ref == values["ref"], LibraryItem.id != iid))
    if clash:
        return redirect(f"/libraries/{lid}/items/{iid}", error=f"{values['ref']} already exists.")
    audit.apply_changes(db, user.upn, item, values)
    db.commit()
    return redirect(f"/libraries/{lid}", msg=f"Saved {item.ref}.")


@router.post("/libraries/{lid}/import")
async def import_items(lid: int, request: Request, user: User = Depends(require("admin")),
                       db: Session = Depends(get_session)):
    lib = _library(db, lid)
    form = await request.form()
    text = str(form.get("text", ""))
    upload = form.get("file")
    if isinstance(upload, UploadFile) and upload.filename:
        text = (await upload.read()).decode("utf-8-sig", errors="replace")
    rows, errors = libraries.parse_table(text)
    if errors:
        return redirect(f"/libraries/{lid}", error="Nothing imported.\n" + "\n".join(errors[:20]))
    counts = libraries.import_items(db, user, lib, rows)
    db.commit()
    return redirect(f"/libraries/{lid}", msg=(f"Imported: {counts['added']} added, "
                                              f"{counts['updated']} updated, {counts['unchanged']} unchanged."))


# --- scope -----------------------------------------------------------------------------

@router.get("/scope")
def scope_page(request: Request, user: User = Depends(current_user), db: Session = Depends(get_session)):
    return render(request, "controls/scope.html", {"tree": scope.tree(db), "kinds": scope.KINDS})


@router.post("/scope/sync")
def scope_sync(user: User = Depends(require("admin")), db: Session = Depends(get_session)):
    try:
        counts = scope.sync(db, user)
    except scope.SyncError as err:
        db.rollback()
        return redirect("/scope", error=str(err))
    db.commit()
    return redirect("/scope", msg=f"Synced from Azure: {counts['added']} added, {counts['updated']} "
                                  f"updated, {counts['deactivated']} deactivated.")


@router.post("/scope/nodes")
def add_node(kind: str = Form(...), name: str = Form(...), azure_id: str = Form(""),
             parent_id: str = Form(""), user: User = Depends(require("admin")),
             db: Session = Depends(get_session)):
    if kind not in scope.KINDS or not name.strip():
        return redirect("/scope", error="Kind and name are required.")
    if azure_id.strip() and db.scalar(select(ScopeNode.id).where(ScopeNode.azure_id == azure_id.strip())):
        return redirect("/scope", error=f"{azure_id} already exists.")
    audit.create(db, user.upn, ScopeNode(kind=kind, name=name.strip(), azure_id=azure_id.strip() or None,
                                         parent_id=int(parent_id) if parent_id else None, source="manual"))
    db.commit()
    return redirect("/scope", msg=f"Added {name.strip()}.")


@router.post("/scope/nodes/{nid}")
def update_node(nid: int, name: str = Form(...), parent_id: str = Form(""), active: str = Form(""),
                user: User = Depends(require("admin")), db: Session = Depends(get_session)):
    node = db.get(ScopeNode, nid)
    if node is None:
        raise HTTPException(404)
    parent = int(parent_id) if parent_id else None
    if parent is not None and parent in scope._subtree_ids(db, nid):
        return redirect("/scope", error="A node can't be moved under itself.")
    audit.apply_changes(db, user.upn, node, {"name": name.strip() or node.name, "parent_id": parent,
                                             "active": active == "on"})
    db.commit()
    return redirect("/scope", msg=f"Saved {node.name}.")


# --- assessments -----------------------------------------------------------------------

@router.get("/assessments/new/controls")
def new_controls_form(request: Request, user: User = Depends(require("preparer")),
                      db: Session = Depends(get_session)):
    libs = db.scalars(select(Library).where(Library.kind == "controls").order_by(Library.name)).all()
    roots = [(n, d) for n, d in scope.tree(db) if n.active and n.kind != "subscription"]
    return render(request, "controls/new.html", {"libraries": libs, "roots": roots})


@router.post("/assessments/controls")
def create_controls(name: str = Form(...), library_id: int = Form(...), root_id: int = Form(...),
                    period_start: str = Form(""), period_end: str = Form(""),
                    user: User = Depends(require("preparer")), db: Session = Depends(get_session)):
    lib, root = db.get(Library, library_id), db.get(ScopeNode, root_id)
    if lib is None or lib.kind != "controls" or root is None:
        return redirect("/assessments/new/controls", error="Pick a controls library and a scope.")
    if not any(i.active for i in lib.items):
        return redirect("/assessments/new/controls", error=f"{lib.name} has no active controls yet.")
    a = assessments.create_controls(db, user, lib, name.strip(), root, _date(period_start), _date(period_end))
    db.commit()
    return redirect(f"/assessments/{a.id}", msg="Assessment created. Controls and scope are frozen "
                                                "as of now; later library edits don't change it.")


@router.get("/assessments/{aid:int}")
def assessment_page(aid: int, request: Request, category: str = "", q: str = "", show: str = "",
                    user: User = Depends(current_user), db: Session = Depends(get_session)):
    a = load_assessment(db, aid)
    if a.kind == "checklist":
        return ai.review_page(request, db, user, a)
    rows = assessments.rollup(db, a)
    categories = sorted({r["item"].category for r in rows if r["item"].category})
    filtered = [r for r in rows
                if (not category or r["item"].category == category)
                and (not q or q.lower() in " ".join((r["item"].ref, r["item"].title,
                                                     r["item"].framework_refs, r["item"].owner)).lower())
                and (not show or (show == "to_review" and r["states"].get("prepared"))
                     or (show == "gaps" and (r["outcomes"].get("ineffective") or r["outcomes"].get("partially_effective")))
                     or (show == "open" and (r["outcomes"].get("unanswered") or r["outcomes"].get("in_progress"))))]
    open_issues = db.scalar(select(func.count(Issue.id)).where(Issue.assessment_id == aid,
                                                                Issue.status != "closed"))
    return render(request, "controls/overview.html", {
        "a": a, "rows": filtered, "all_rows": rows, "categories": categories,
        "category": category, "q": q, "show": show, "progress": assessments.progress(db, a),
        "outcome_labels": OUTCOME_LABELS, "open_issues": open_issues,
        "leaves": rows[0]["leaves"] if rows else 0,
    })


@router.get("/assessments/{aid}/items/{iid}")
def item_page(aid: int, iid: int, request: Request, node: int | None = None,
              user: User = Depends(current_user), db: Session = Depends(get_session)):
    a = _controls(db, aid)
    item = db.get(AssessmentItem, iid)
    if item is None or item.assessment_id != aid:
        raise HTTPException(404)
    tree = assessments.Tree(a.scope_nodes)
    current = tree.nodes.get(node) if node else tree.root
    if current is None:
        raise HTTPException(404, "Scope node not in this assessment")
    own = {r.scope_node_id: r for r in db.scalars(
        select(Response).where(Response.assessment_item_id == iid))}
    nodes = []
    for n, depth in tree.walk():
        r, src = assessments.effective(tree, own, n.id)
        nodes.append({"node": n, "depth": depth, "r": r, "inherited_from": src,
                      "outcome": assessments.outcome(a.kind, r)})
    r_own = own.get(current.id)
    r_eff, inherited_from = assessments.effective(tree, own, current.id)
    overrides_below = [x for x in nodes if x["node"].id != current.id and x["inherited_from"] is None
                       and x["r"] is not None and current.id in {anc.id for anc in tree.ancestors(x["node"].id)}]
    idx = [i.id for i in a.items].index(iid)
    issues = db.scalars(select(Issue).where(Issue.assessment_item_id == iid).order_by(Issue.id)).all()
    return render(request, "controls/item.html", {
        "a": a, "item": item, "tree": tree, "nodes": nodes, "current": current,
        "r": r_own, "r_eff": r_eff, "inherited_from": inherited_from,
        "overrides_below": overrides_below, "issues": issues,
        "locked": responses.lock_reason(a, r_own),
        "prev": a.items[idx - 1] if idx > 0 else None,
        "next": a.items[idx + 1] if idx + 1 < len(a.items) else None,
        "return_to": f"/assessments/{aid}/items/{iid}?node={current.id}",
        "status_choices": responses.STATUS_CHOICES["controls"], "ratings": responses.RATINGS,
        "outcome_labels": OUTCOME_LABELS, "path": tree.path(current.id),
    })


@router.post("/assessments/{aid}/items/{iid}/override")
def override(aid: int, iid: int, node_id: int = Form(...), mode: str = Form("copy"),
             user: User = Depends(require("preparer")), db: Session = Depends(get_session)):
    """Give a node its own answer instead of the inherited one: a copy to edit, or N/A."""
    a = _controls(db, aid)
    item = db.get(AssessmentItem, iid)
    tree = assessments.Tree(a.scope_nodes)
    if item is None or item.assessment_id != aid or node_id not in tree.nodes:
        raise HTTPException(404)
    back = f"/assessments/{aid}/items/{iid}?node={node_id}"
    own = {r.scope_node_id: r for r in db.scalars(select(Response).where(Response.assessment_item_id == iid))}
    inherited, src = assessments.effective(tree, own, node_id)
    if node_id in own:
        return redirect(back, error="This level already has its own answer.")
    if mode == "na":
        values = {"status": "na"}
    elif inherited is not None:
        values = {f: getattr(inherited, f) for f in responses.FIELDS["controls"]}
    else:
        values = {}
    note = f"override of answer inherited from {src.name}" if src else "override"
    try:
        responses.save(db, user, a, item, tree.nodes[node_id], values, 0, action="override", note=note)
    except responses.SaveError as err:
        db.rollback()
        return redirect(back, error=err.message)
    db.commit()
    return redirect(back, msg="Now answered at this level." + (
        " Say why it's not applicable, then mark it prepared." if mode == "na" else ""))


@router.post("/assessments/{aid}/items/{iid}/inherit")
def revert_to_inherited(aid: int, iid: int, node_id: int = Form(...),
                        user: User = Depends(require("preparer")), db: Session = Depends(get_session)):
    """Drop a node's own (draft, unevidenced) answer so it inherits again. The deleted
    content stays in the audit log."""
    a = _controls(db, aid)
    back = f"/assessments/{aid}/items/{iid}?node={node_id}"
    r = responses.find(db, iid, node_id)
    if r is None or r.assessment_id != aid:
        raise HTTPException(404)
    if r.node.parent_id is None:
        return redirect(back, error="The top of the scope has nothing to inherit from.")
    if responses.lock_reason(a, r) or r.review_state not in ("draft", "returned"):
        return redirect(back, error="Only draft answers can be removed - reopen it first.")
    linked = (db.scalar(select(EvidenceLink.id).where(EvidenceLink.response_id == r.id))
              or db.scalar(select(Comment.id).where(Comment.response_id == r.id))
              or db.scalar(select(Issue.id).where(Issue.response_id == r.id)))
    if linked:
        return redirect(back, error="This answer has evidence, comments or issues attached, so it "
                                    "stays. Edit it instead.")
    snapshot = {f: (getattr(r, f), None) for f in responses.FIELDS["controls"] if getattr(r, f)}
    audit.record(db, user.upn, "delete", r, snapshot, note="reverted to inherited answer")
    db.delete(r)
    db.commit()
    return redirect(back, msg="Removed - this level inherits again.")


# --- close / attest, export, report ----------------------------------------------------

DEFAULT_ATTESTATION = (
    "I confirm that the controls in this assessment were assessed for the period stated, "
    "that the answers and evidence recorded here reflect how they operated, and that every "
    "gap identified has been raised as an issue with an owner and due date.")


@router.get("/assessments/{aid}/close")
def close_page(aid: int, request: Request, user: User = Depends(current_user),
               db: Session = Depends(get_session)):
    a = _controls(db, aid)
    blockers = assessments.close_blockers(db, a) if a.status != "closed" else None
    open_issues = db.scalars(select(Issue).where(Issue.assessment_id == aid,
                                                 Issue.status != "closed")).all()
    return render(request, "controls/close.html", {
        "a": a, "blockers": blockers, "open_issues": open_issues,
        "default_statement": DEFAULT_ATTESTATION, "tree": assessments.Tree(a.scope_nodes)})


@router.post("/assessments/{aid}/close")
def close(aid: int, statement: str = Form(...), confirm: str = Form(""),
          user: User = Depends(require("reviewer")), db: Session = Depends(get_session)):
    a = _controls(db, aid)
    responses.lock(db, a)
    back = f"/assessments/{aid}/close"
    if a.status == "closed":
        return redirect(back, error="Already closed.")
    blockers = assessments.close_blockers(db, a)
    if blockers["incomplete"] or blockers["unreviewed"]:
        return redirect(back, error="Not ready to close - see the list below.")
    if confirm != "on" or not statement.strip():
        return redirect(back, error="Tick the confirmation and keep an attestation statement.")
    open_issues = db.scalars(select(Issue.id).where(Issue.assessment_id == aid,
                                                    Issue.status != "closed")).all()
    attestation = {"closed_by": user.upn, "closed_by_name": user.display_name,
                   "closed_at": datetime.now(timezone.utc).isoformat(),
                   "statement": statement.strip(), "open_issues_at_close": len(open_issues)}
    audit.apply_changes(db, user.upn, a, {"status": "closed", "attestation": attestation},
                        action="close", note=statement.strip())
    db.commit()
    return redirect(f"/assessments/{aid}", msg="Assessment closed and attested. Answers are now locked.")


@router.post("/assessments/{aid}/reopen")
def reopen(aid: int, reason: str = Form(""), user: User = Depends(require("admin")),
           db: Session = Depends(get_session)):
    a = _controls(db, aid)
    if a.status != "closed":
        return redirect(f"/assessments/{aid}/close", error="It isn't closed.")
    if not reason.strip():
        return redirect(f"/assessments/{aid}/close", error="A reason is required to reopen.")
    audit.apply_changes(db, user.upn, a, {"status": "open", "attestation": None},
                        action="reopen", note=reason.strip())
    db.commit()
    return redirect(f"/assessments/{aid}", msg="Reopened. The previous attestation stays in the activity log.")


@router.get("/assessments/{aid}/export.xlsx")
def export_xlsx(aid: int, user: User = Depends(current_user), db: Session = Depends(get_session)):
    a = _controls(db, aid)
    data = export.controls_workbook(db, a, user)
    audit.record(db, user.upn, "export", a, note="Excel export")
    db.commit()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return HttpResponse(data, media_type=(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="assessment-{aid}-{stamp}.xlsx"'})


@router.get("/assessments/{aid}/report")
def report(aid: int, request: Request, user: User = Depends(current_user),
           db: Session = Depends(get_session)):
    a = _controls(db, aid)
    tree = assessments.Tree(a.scope_nodes)
    by_item = assessments.responses_by_item(db, aid)
    sections = []
    for row in assessments.rollup(db, a):
        own = by_item.get(row["item"].id, {})
        answers = [(tree.path(nid), r) for nid, r in sorted(own.items(),
                                                            key=lambda kv: tree.path(kv[0]))]
        sections.append({**row, "answers": answers})
    issues = db.scalars(select(Issue).where(Issue.assessment_id == aid).order_by(Issue.id)).all()
    return render(request, "controls/report.html", {
        "a": a, "sections": sections, "issues": issues, "tree": tree,
        "ratings": responses.RATINGS, "outcome_labels": OUTCOME_LABELS,
        "generated": datetime.now(timezone.utc)})
