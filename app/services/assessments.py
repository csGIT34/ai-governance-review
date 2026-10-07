"""Creating assessments (with their frozen snapshots) and resolving inherited answers."""
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit
from app.models import (ITEM_FIELDS, Assessment, AssessmentItem, AssessmentScopeNode, Library,
                        Response, ScopeNode, User)

STATUSES = {"checklist": ["in_review", "approved", "conditional", "rejected"],
            "controls": ["open", "closed"]}


def _snapshot_items(db: Session, assessment: Assessment, library: Library):
    for item in library.items:
        if item.active:
            db.add(AssessmentItem(assessment_id=assessment.id, library_item_id=item.id,
                                  **{f: getattr(item, f) for f in ITEM_FIELDS}))


def _library_snapshot(library: Library) -> dict:
    return {"key": library.key, "name": library.name, "source_url": library.source_url,
            "source_version": library.source_version}


def create_checklist(db: Session, user: User, library: Library, name: str,
                     context: dict) -> Assessment:
    """AI-enablement style review: one implicit scope node."""
    a = audit.create(db, user.upn, Assessment(
        kind="checklist", library_id=library.id, name=name, status="in_review",
        context=context, library_snapshot=_library_snapshot(library), created_by=user.upn))
    _snapshot_items(db, a, library)
    db.add(AssessmentScopeNode(assessment_id=a.id, kind="review", name="This review"))
    db.flush()
    return a


def create_controls(db: Session, user: User, library: Library, name: str, root: ScopeNode,
                    period_start: date | None, period_end: date | None) -> Assessment:
    """Control assessment over the active subtree under `root` (tenant or management group)."""
    a = audit.create(db, user.upn, Assessment(
        kind="controls", library_id=library.id, name=name, status="open",
        period_start=period_start, period_end=period_end,
        context={"scope_root": root.name}, library_snapshot=_library_snapshot(library),
        created_by=user.upn))
    _snapshot_items(db, a, library)

    children: dict[int | None, list[ScopeNode]] = {}
    for n in db.scalars(select(ScopeNode).where(ScopeNode.active).order_by(ScopeNode.name)):
        children.setdefault(n.parent_id, []).append(n)

    def copy(node: ScopeNode, parent: AssessmentScopeNode | None):
        snap = AssessmentScopeNode(assessment_id=a.id, scope_node_id=node.id, kind=node.kind,
                                   name=node.name, azure_id=node.azure_id,
                                   parent_id=parent.id if parent else None)
        db.add(snap)
        db.flush()
        for child in children.get(node.id, []):
            copy(child, snap)

    copy(root, None)
    return a


# --- scope tree helpers -------------------------------------------------------------

class Tree:
    """The frozen scope tree of one assessment."""

    def __init__(self, nodes: list[AssessmentScopeNode]):
        self.nodes = {n.id: n for n in nodes}
        self.children: dict[int | None, list[AssessmentScopeNode]] = {}
        for n in nodes:
            self.children.setdefault(n.parent_id, []).append(n)
        for kids in self.children.values():
            kids.sort(key=lambda n: (n.kind == "subscription", n.name.lower()))
        self.root = self.children[None][0]

    def ancestors(self, node_id: int) -> list[AssessmentScopeNode]:
        """Nearest first, excluding the node itself."""
        out, cur = [], self.nodes[node_id].parent_id
        while cur is not None:
            out.append(self.nodes[cur])
            cur = self.nodes[cur].parent_id
        return out

    def walk(self, node: AssessmentScopeNode | None = None, depth: int = 0):
        """Depth-first (node, depth) pairs from the root."""
        node = node or self.root
        yield node, depth
        for child in self.children.get(node.id, []):
            yield from self.walk(child, depth + 1)

    def leaves(self) -> list[AssessmentScopeNode]:
        """Nodes that get assessed in the rollup: subscriptions, or the single root of a
        checklist / an empty management group."""
        subs = [n for n in self.nodes.values() if n.kind == "subscription"]
        return subs or [self.root]

    def path(self, node_id: int) -> str:
        return " / ".join(n.name for n in reversed([self.nodes[node_id], *self.ancestors(node_id)]))


def effective(tree: Tree, responses: dict[int, Response], node_id: int):
    """(response, inherited_from) for a node: its own response, else the nearest
    ancestor's. inherited_from is None when the node answers for itself."""
    if node_id in responses:
        return responses[node_id], None
    for anc in tree.ancestors(node_id):
        if anc.id in responses:
            return responses[anc.id], anc
    return None, None


def responses_by_item(db: Session, assessment_id: int) -> dict[int, dict[int, Response]]:
    """{assessment_item_id: {scope_node_id: Response}} for a whole assessment."""
    out: dict[int, dict[int, Response]] = {}
    for r in db.scalars(select(Response).where(Response.assessment_id == assessment_id)):
        out.setdefault(r.assessment_item_id, {})[r.scope_node_id] = r
    return out


# --- progress & rollup ----------------------------------------------------------------

def outcome(kind: str, r: Response | None) -> str:
    """One word for an effective response: unanswered | in_progress | na | the rating
    (controls), or the checklist status."""
    from app.services.responses import missing_fields
    if r is None:
        return "unanswered" if kind == "controls" else "unreviewed"
    if kind == "checklist":
        return r.status or "unreviewed"
    if r.status == "na":
        return "na" if not missing_fields(kind, r) else "in_progress"
    if missing_fields(kind, r):
        return "in_progress"
    return r.operating_rating if r.operating_rating not in ("", "not_tested") else r.design_rating


def rollup(db: Session, a: Assessment) -> list[dict]:
    """Per item: counts of effective outcomes and review states across the leaves."""
    tree = Tree(a.scope_nodes)
    leaves = tree.leaves()
    by_item = responses_by_item(db, a.id)
    rows = []
    for item in a.items:
        own = by_item.get(item.id, {})
        outcomes: dict[str, int] = {}
        states: dict[str, int] = {}
        for leaf in leaves:
            r, _ = effective(tree, own, leaf.id)
            o = outcome(a.kind, r)
            outcomes[o] = outcomes.get(o, 0) + 1
        for r in own.values():
            states[r.review_state] = states.get(r.review_state, 0) + 1
        rows.append({"item": item, "outcomes": outcomes, "states": states,
                     "answered_at": len(own), "leaves": len(leaves)})
    return rows


def progress(db: Session, a: Assessment) -> dict:
    """done/total for list pages. Checklist: items answered. Controls: (item, subscription)
    pairs with a complete effective answer."""
    not_done = {"unreviewed", "unanswered", "in_progress"}
    rows = rollup(db, a)
    total = sum(r["leaves"] for r in rows)
    done = sum(n for r in rows for o, n in r["outcomes"].items() if o not in not_done)
    return {"done": done, "total": total or 1}


def close_blockers(db: Session, a: Assessment) -> dict:
    """What stops a control assessment from being closed: (item, subscription) pairs
    without a complete effective answer, and answers not yet signed off."""
    tree = Tree(a.scope_nodes)
    by_item = responses_by_item(db, a.id)
    incomplete, unreviewed = [], []
    for item in a.items:
        own = by_item.get(item.id, {})
        for leaf in tree.leaves():
            r, _ = effective(tree, own, leaf.id)
            if outcome(a.kind, r) in ("unanswered", "in_progress"):
                incomplete.append((item, leaf))
        unreviewed += [r for r in own.values() if r.review_state != "reviewed"]
    return {"incomplete": incomplete, "unreviewed": unreviewed}
