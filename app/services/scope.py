"""The Azure scope hierarchy: tenant -> management groups -> subscriptions.

Synced from the Management Groups API (descendants of AZURE_ROOT_MANAGEMENT_GROUP)
using DefaultAzureCredential - on Azure, the app's managed identity needs
'Management Group Reader' (or Reader) on that management group. Nodes can also be
added by hand. Nothing is ever deleted: nodes that disappear from Azure are
deactivated so old assessments keep resolving.
"""
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import audit, config
from app.models import ScopeNode, User, utcnow

API = "https://management.azure.com/providers/Microsoft.Management/managementGroups"
API_VERSION = "2020-05-01"
KINDS = {"tenant": "Tenant", "management_group": "Management group", "subscription": "Subscription"}


class SyncError(Exception):
    pass


def _token() -> str:
    try:
        from azure.identity import DefaultAzureCredential
        return DefaultAzureCredential().get_token("https://management.azure.com/.default").token
    except Exception as err:
        raise SyncError(f"Could not acquire Azure credentials: {err}") from err


def fetch_hierarchy(root_id: str) -> tuple[dict, list[dict]]:
    """(root, descendants) as plain dicts: {azure_id, kind, name, parent}."""
    headers = {"Authorization": f"Bearer {_token()}"}
    try:
        root = httpx.get(f"{API}/{root_id}", params={"api-version": API_VERSION},
                         headers=headers, timeout=30)
        root.raise_for_status()
        items, url = [], f"{API}/{root_id}/descendants?api-version={API_VERSION}"
        while url:
            page = httpx.get(url, headers=headers, timeout=60)
            page.raise_for_status()
            data = page.json()
            items += data.get("value", [])
            url = data.get("nextLink")
    except httpx.HTTPError as err:
        raise SyncError(f"Management Groups API request failed: {err}") from err
    root_node = {"azure_id": root_id, "name": root.json()["properties"]["displayName"],
                 "kind": "tenant" if root_id == config.AZURE_TENANT_ID else "management_group",
                 "parent": None}
    nodes = [{"azure_id": d["name"],
              "kind": "subscription" if d["type"].endswith("/subscriptions") else "management_group",
              "name": d["properties"].get("displayName") or d["name"],
              "parent": ((d["properties"].get("parent") or {}).get("id") or "").rsplit("/", 1)[-1] or None}
             for d in items]
    return root_node, nodes


def sync(db: Session, user: User) -> dict:
    """Upsert the hierarchy under the configured root. Returns counts."""
    if not config.AZURE_ROOT_MANAGEMENT_GROUP:
        raise SyncError("Set AZURE_TENANT_ID (or AZURE_ROOT_MANAGEMENT_GROUP) to sync from Azure.")
    root, descendants = fetch_hierarchy(config.AZURE_ROOT_MANAGEMENT_GROUP)
    existing = {n.azure_id: n for n in db.scalars(select(ScopeNode).where(ScopeNode.azure_id.is_not(None)))}
    counts = {"added": 0, "updated": 0, "deactivated": 0}
    seen: dict[str, ScopeNode] = {}
    now = utcnow()

    for d in [root, *descendants]:  # pass 1: nodes
        node = existing.get(d["azure_id"])
        if node is None:
            node = audit.create(db, user.upn, ScopeNode(
                azure_id=d["azure_id"], kind=d["kind"], name=d["name"], source="azure"),
                note="azure sync")
            counts["added"] += 1
        elif audit.apply_changes(db, user.upn, node, {"name": d["name"], "kind": d["kind"],
                                                      "source": "azure", "active": True},
                                 note="azure sync"):
            counts["updated"] += 1
        node.last_synced_at = now
        seen[d["azure_id"]] = node
    for d in descendants:  # pass 2: parents (descendants can arrive before their parent)
        parent = seen.get(d["parent"])
        if parent is not None:
            audit.apply_changes(db, user.upn, seen[d["azure_id"]], {"parent_id": parent.id},
                                note="azure sync")

    # Azure-sourced nodes under the root that Azure no longer reports.
    under_root = _subtree_ids(db, seen[root["azure_id"]].id)
    for node in existing.values():
        if (node.source == "azure" and node.active and node.azure_id not in seen
                and node.id in under_root):
            audit.apply_changes(db, user.upn, node, {"active": False},
                                note="azure sync: no longer in Azure")
            counts["deactivated"] += 1
    db.flush()
    return counts


def _subtree_ids(db: Session, root_id: int) -> set[int]:
    children: dict[int | None, list[int]] = {}
    for n in db.scalars(select(ScopeNode)):
        children.setdefault(n.parent_id, []).append(n.id)
    out, stack = set(), [root_id]
    while stack:
        cur = stack.pop()
        out.add(cur)
        stack += children.get(cur, [])
    return out


def tree(db: Session) -> list[tuple[ScopeNode, int]]:
    """All nodes depth-first as (node, depth); roots first."""
    nodes = db.scalars(select(ScopeNode)).all()
    children: dict[int | None, list[ScopeNode]] = {}
    for n in nodes:
        children.setdefault(n.parent_id, []).append(n)
    for kids in children.values():
        kids.sort(key=lambda n: (n.kind == "subscription", n.name.lower()))
    out = []

    def walk(n: ScopeNode, depth: int):
        out.append((n, depth))
        for c in children.get(n.id, []):
            walk(c, depth + 1)

    for root in children.get(None, []):
        walk(root, 0)
    return out
