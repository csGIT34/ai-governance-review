"""AI-model enablement reviews (checklist kind): catalog enrichment, reference-doc
snapshots and the blocker gate on decisions.

The auto-fill rules in app.enrichment.apply and the reference-doc positions work on
a plain dict view of the checklist ({ref: {status, notes, evidence}}). This module
builds that view from the responses, lets the rules edit it, then writes the
differences back as audited 'autofill' changes. Reviewer input is never overwritten:
the rules only touch items still unreviewed.
"""
import copy
import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import audit
from app.enrichment import apply, azure_catalog, gcp_catalog, reference_docs
from app.models import Assessment, Artifact, User
from app.services import evidence, responses

AI_LIBRARY_KEY = "ai-enablement"


def node(a: Assessment):
    return a.scope_nodes[0]


def blocker_gaps(db: Session, a: Assessment) -> list:
    """Blocker items not yet Pass or N/A."""
    gaps = []
    for item in a.items:
        if item.severity == "blocker":
            r = responses.find(db, item.id, node(a).id)
            if not r or r.status not in ("pass", "na"):
                gaps.append(item)
    return gaps


def _view(db: Session, a: Assessment) -> dict:
    checklist = {}
    for item in a.items:
        r = responses.find(db, item.id, node(a).id)
        checklist[item.ref] = {"status": r.status if r else "unreviewed",
                               "notes": r.narrative if r else "",
                               "evidence": "linked" if r and r.evidence else ""}
    return {"model_name": a.context.get("model_name", ""),
            "model_version": a.context.get("model_version", ""),
            "checklist": checklist}


def _write_back(db: Session, user: User, a: Assessment, before: dict, after: dict,
                links: dict[str, dict]):
    """Persist view changes. links[ref] = kwargs for evidence.add_link when the rule
    attached evidence to that item."""
    items = {i.ref: i for i in a.items}
    for ref, new in after.items():
        old = before[ref]
        values = {}
        if new["status"] != old["status"]:
            values["status"] = new["status"]
        if new["notes"] != old["notes"]:
            values["narrative"] = new["notes"]
        resp = responses.find(db, items[ref].id, node(a).id)
        if values or (new["evidence"] != old["evidence"] and ref in links):
            resp = responses.save(db, user, a, items[ref], node(a), values,
                                  resp.version if resp else 0, action="autofill")
        if new["evidence"] != old["evidence"] and ref in links:
            evidence.add_link(db, user, a, response=resp, note="autofill", **links[ref])


def enrich(db: Session, user: User, a: Assessment) -> dict:
    """Fetch the CSP catalog snapshot, auto-fill A2/B1/B2, store the snapshot as an artifact."""
    ctx = a.context
    if ctx["csp"] == "azure":
        region = (ctx.get("regions") or "eastus").split(",")[0].strip()
        snapshot = azure_catalog.enrich(ctx["model_name"], region)
    else:
        snapshot = gcp_catalog.enrich(ctx["model_name"], ctx.get("publisher") or "google")
    if not snapshot.get("error"):
        view = _view(db, a)
        before = copy.deepcopy(view["checklist"])
        applier = apply.apply_azure if ctx["csp"] == "azure" else apply.apply_gcp
        snapshot["checklist_updates"] = applier(snapshot, view)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        artifact = evidence.store_artifact(db, user, a, f"catalog-snapshot-{stamp}.json",
                                           json.dumps(snapshot, indent=2).encode(),
                                           "application/json", note="catalog snapshot")
        link = {"url": "", "title": snapshot.get("source", "catalog snapshot"), "artifact": artifact}
        _write_back(db, user, a, before, view["checklist"],
                    {u["item"]: link for u in snapshot["checklist_updates"]})
    audit.apply_changes(db, user.upn, a, {"enrichment": snapshot}, action="enrich")
    return snapshot


def fetch_reference_docs(db: Session, user: User, a: Assessment) -> list[str]:
    """Snapshot the CSP's data-privacy/terms pages into artifacts, link them as evidence
    on related unreviewed items and apply curated standard positions. Returns problems."""
    docs = reference_docs.get_docs(db)
    view = _view(db, a)
    checklist = view["checklist"]
    before = copy.deepcopy(checklist)
    links: dict[str, dict] = {}

    # Internal documents are usually behind sign-in: link as evidence, no snapshot.
    for ref in docs.get("internal", []):
        for iid in ref["items"]:
            resp = checklist.get(iid)
            if resp and resp["status"] == "unreviewed" and not resp["evidence"]:
                resp["evidence"] = ref["title"]
                links[iid] = {"url": ref["url"], "title": ref["title"]}

    problems = []
    for ref in docs.get(a.context["csp"], []):
        try:
            html = reference_docs.fetch(ref["url"])
        except Exception as err:
            problems.append(f"{ref['title']}: {err}")
            continue
        changed = (ref.get("validated_hash")
                   and reference_docs.text_hash(html) != ref["validated_hash"])
        if changed:
            problems.append(f"{ref['title']}: page changed since its standard position "
                            f"was validated - positions not applied")
        try:
            data, name, ctype = reference_docs.fetch_pdf(ref["url"]), f"{ref['slug']}.pdf", "application/pdf"
        except Exception:
            data, name, ctype = html, f"{ref['slug']}.html", "text/html"
        artifact: Artifact = evidence.store_artifact(db, user, a, name, data, ctype,
                                                     note=f"reference doc snapshot of {ref['url']}")
        for iid in ref["items"]:
            resp = checklist.get(iid)
            if not resp or resp["status"] != "unreviewed":
                continue
            pos = ref.get("positions", {}).get(iid)
            if pos and not resp["notes"]:
                if changed:
                    resp["status"] = "needs_info"
                    resp["notes"] = f"[auto] {reference_docs.CHANGED_NOTE}"
                else:
                    resp["status"] = pos["status"]
                    resp["notes"] = f"[auto] {pos['note']}"
            if not resp["evidence"]:
                resp["evidence"] = name
                links[iid] = {"url": ref["url"], "title": f"{ref['title']} (snapshot)",
                              "artifact": artifact}
    _write_back(db, user, a, before, checklist, links)
    return problems
