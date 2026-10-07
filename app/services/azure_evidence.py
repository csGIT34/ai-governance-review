"""Attach the result of an Azure Resource Graph query as evidence for an answer.

A control in the library can carry `evidence_query` (KQL), e.g. the Defender plan tier of
every subscription. Running it for a scope node queries the subscriptions under that node and
stores the full result as a JSON artifact (sha256, never overwritten), linked as evidence.
The managed identity needs Reader on those subscriptions (or the management group).
"""
import json
from datetime import datetime, timezone

import httpx
from sqlalchemy.orm import Session

from app.models import Assessment, AssessmentItem, AssessmentScopeNode, EvidenceLink, User
from app.services import assessments, evidence, responses, scope

API = "https://management.azure.com/providers/Microsoft.ResourceGraph/resources"
API_VERSION = "2022-10-01"
MAX_ROWS = 5000


class EvidenceError(Exception):
    pass


def subscriptions_under(tree: assessments.Tree, node: AssessmentScopeNode) -> list[str]:
    ids = [n.azure_id for n, _ in tree.walk(node) if n.kind == "subscription" and n.azure_id]
    return sorted(set(ids))


def run_query(subscriptions: list[str], kql: str) -> dict:
    """{rows, total_records, truncated}. Pages through results up to MAX_ROWS."""
    try:
        headers = {"Authorization": f"Bearer {scope._token()}"}
    except scope.SyncError as err:
        raise EvidenceError(str(err)) from err
    rows, skip, total = [], None, 0
    try:
        while True:
            options = {"resultFormat": "objectArray", "$top": 1000}
            if skip:
                options["$skipToken"] = skip
            resp = httpx.post(API, params={"api-version": API_VERSION}, headers=headers, timeout=60,
                              json={"subscriptions": subscriptions, "query": kql, "options": options})
            if resp.status_code >= 400:
                detail = resp.json().get("error", {}).get("message", resp.text[:300]) \
                    if resp.headers.get("content-type", "").startswith("application/json") else resp.text[:300]
                raise EvidenceError(f"Resource Graph returned {resp.status_code}: {detail}")
            body = resp.json()
            rows += body.get("data", [])
            total = body.get("totalRecords", len(rows))
            skip = body.get("$skipToken")
            if not skip or len(rows) >= MAX_ROWS:
                break
    except httpx.HTTPError as err:
        raise EvidenceError(f"Resource Graph request failed: {err}") from err
    return {"rows": rows[:MAX_ROWS], "total_records": total, "truncated": total > MAX_ROWS}


def attach(db: Session, user: User, a: Assessment, item: AssessmentItem,
           node: AssessmentScopeNode) -> EvidenceLink:
    if not item.evidence_query.strip():
        raise EvidenceError(f"{item.ref} has no Azure evidence query in the library.")
    tree = assessments.Tree(a.scope_nodes)
    subs = subscriptions_under(tree, node)
    if not subs:
        raise EvidenceError(f"No subscriptions with an Azure id under {node.name}.")
    resp = responses.for_edit(db, user, a, item, node)  # same lock / inheritance rules as edits
    result = run_query(subs, item.evidence_query)
    now = datetime.now(timezone.utc)
    payload = {"control": item.ref, "scope": tree.path(node.id), "subscriptions": subs,
               "query": item.evidence_query, "run_at": now.isoformat(), "run_by": user.upn,
               "source": f"Azure Resource Graph (api-version {API_VERSION})", **result}
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    art = evidence.store_artifact(db, user, a, f"azure-evidence-{item.ref}-{node.name}-{stamp}.json",
                                  json.dumps(payload, indent=2, default=str).encode(),
                                  "application/json", note="Azure Resource Graph evidence")
    rows = len(result["rows"])
    title = (f"Azure Resource Graph: {rows} row{'s' if rows != 1 else ''} across {len(subs)} "
             f"subscription{'s' if len(subs) != 1 else ''}"
             + (" (truncated)" if result["truncated"] else "") + f" · {stamp}")
    return evidence.add_link(db, user, a, "", title, response=resp, artifact=art)
