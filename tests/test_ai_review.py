"""AI-model enablement reviews (checklist kind), end to end through the HTTP routes."""
import io
from urllib.parse import unquote_plus as unquote

from sqlalchemy import select

from app.models import Artifact, AuditEvent, EvidenceLink, Response
from conftest import PREPARER, REVIEWER, VIEWER, aid_from, as_user, item_id, node_id

STUB_HTML = b"<html>terms</html>"


def create(client, **overrides) -> int:
    data = {"csp": "azure", "service_name": "Azure OpenAI", "model_name": "gpt-4.1",
            "model_version": "2025-04-14", "regions": "eastus2",
            "requested_by": "app team", "justification": "need long context"} | overrides
    return aid_from(client.post("/assessments/ai", data=data, follow_redirects=False))


def save(client, session, aid, ref, version=0, **fields):
    return client.post(f"/assessments/{aid}/responses", data={
        "item_id": item_id(session, aid, ref), "node_id": node_id(session, aid),
        "version": version, **fields})


def response(session, aid, ref) -> Response | None:
    session.expire_all()
    return session.scalar(select(Response).where(
        Response.assessment_item_id == item_id(session, aid, ref)))


def test_create_and_view(client):
    aid = create(client)
    page = client.get(f"/assessments/{aid}")
    assert page.status_code == 200
    assert "gpt-4.1" in page.text
    assert "A1" in page.text and "M4" in page.text  # checklist rendered
    assert "gpt-4.1" in client.get("/").text


def test_autosave_and_optimistic_lock(client, session):
    aid = create(client)
    first = save(client, session, aid, "A1", 0, status="pass", narrative="documented in JIRA-123")
    assert first.status_code == 200 and first.json()["version"] == 1
    second = save(client, session, aid, "A1", 1, narrative="documented in JIRA-124")
    assert second.json()["version"] == 2

    stale = save(client, session, aid, "A1", 1, narrative="someone else's text")
    assert stale.status_code == 409
    assert stale.json()["current"]["narrative"] == "documented in JIRA-124"
    assert response(session, aid, "A1").narrative == "documented in JIRA-124"
    assert response(session, aid, "A2") is None  # untouched items have no row


def test_every_change_is_audited(client, session):
    aid = create(client)
    save(client, session, aid, "A1", 0, status="pass")
    save(client, session, aid, "A1", 1, status="fail", narrative="wrong team")
    rid = response(session, aid, "A1").id
    events = session.scalars(select(AuditEvent).where(
        AuditEvent.entity_type == "responses", AuditEvent.entity_id == str(rid))
        .order_by(AuditEvent.id)).all()
    assert [e.action for e in events] == ["create", "update"]
    assert events[1].changes["status"] == ["pass", "fail"]
    assert events[1].actor == "alice@dev.local"
    history = client.get(f"/responses/{rid}/history")
    assert "wrong team" in history.text


def test_invalid_status_rejected(client, session):
    aid = create(client)
    assert save(client, session, aid, "A1", 0, status="approved!!").status_code == 400


def test_viewer_cannot_edit(client, session):
    aid = create(client)
    as_user(client, VIEWER)
    assert save(client, session, aid, "A1", 0, status="pass").status_code == 403
    assert client.post("/assessments/ai", data={"csp": "azure", "service_name": "x",
                                                 "model_name": "y"}).status_code == 403


def test_approve_blocked_by_open_blockers(client):
    aid = create(client)
    resp = client.post(f"/assessments/{aid}/decision", data={"outcome": "approved"},
                       follow_redirects=False)
    assert "Cannot fully approve" in unquote(resp.headers["location"])
    # conditional needs conditions
    resp = client.post(f"/assessments/{aid}/decision", data={"outcome": "conditional"},
                       follow_redirects=False)
    assert "required" in unquote(resp.headers["location"])
    resp = client.post(f"/assessments/{aid}/decision", data={
        "outcome": "conditional", "conditions": "preview only in dev",
        "re_review_date": "2026-12-01"}, follow_redirects=False)
    page = client.get(f"/assessments/{aid}")
    assert "preview only in dev" in page.text and "conditional" in page.text


def test_preparer_cannot_decide(client):
    aid = create(client)
    as_user(client, PREPARER)
    resp = client.post(f"/assessments/{aid}/decision", data={
        "outcome": "rejected", "conditions": "no"}, follow_redirects=False)
    assert resp.status_code == 403


def test_approve_after_clearing_blockers_and_supersede(client, session):
    from app import checklist
    aid = create(client)
    for i in checklist.ITEMS:
        if i["severity"] == "blocker":
            assert save(client, session, aid, i["id"], 0, status="pass").status_code == 200
    as_user(client, REVIEWER)
    resp = client.post(f"/assessments/{aid}/decision", data={"outcome": "approved"},
                       follow_redirects=False)
    assert "Cannot" not in unquote(resp.headers["location"])
    client.post(f"/assessments/{aid}/decision", data={"outcome": "rejected",
                                                       "conditions": "vendor terms changed"})
    page = client.get(f"/assessments/{aid}")
    assert "1 superseded decision" in page.text  # the approval stays visible


def test_artifacts_are_never_overwritten(client, session, blobs):
    aid = create(client)
    for body in (b"v1 costs", b"v2 costs"):
        resp = client.post(f"/assessments/{aid}/artifacts",
                           files={"file": ("pricing.txt", io.BytesIO(body), "text/plain")},
                           data={"item_id": item_id(session, aid, "K1"),
                                 "node_id": node_id(session, aid)},
                           follow_redirects=False)
        assert resp.status_code == 303
    arts = session.scalars(select(Artifact).order_by(Artifact.id)).all()
    assert [a.filename for a in arts] == ["pricing.txt", "pricing.txt"]
    assert arts[0].blob_path != arts[1].blob_path
    assert client.get(f"/artifacts/{arts[0].id}").content == b"v1 costs"
    assert client.get(f"/artifacts/{arts[1].id}").content == b"v2 costs"
    links = session.scalars(select(EvidenceLink)).all()
    assert len(links) == 2 and all(link.kind == "artifact" for link in links)


def test_html_artifact_downloads_as_attachment(client):
    aid = create(client)
    client.post(f"/assessments/{aid}/artifacts",
                files={"file": ("../../evil page.html", io.BytesIO(b"<script>x</script>"), "text/html")})
    resp = client.get("/artifacts/1")
    assert resp.headers["content-disposition"].startswith("attachment")
    assert 'filename="evil page.html"' in resp.headers["content-disposition"]
    assert resp.headers["content-type"] == "application/octet-stream"


def test_evidence_link_pinning(client, session):
    aid = create(client)
    base = {"item_id": item_id(session, aid, "A1"), "node_id": node_id(session, aid)}
    sha = "0123456789abcdef0123456789abcdef01234567"
    client.post(f"/assessments/{aid}/responses/evidence", data=base | {
        "url": f"https://github.example.com/org/audit/blob/{sha}/evidence/mfa.md"})
    resp = client.post(f"/assessments/{aid}/responses/evidence", data=base | {
        "url": "https://github.example.com/org/audit/blob/main/evidence/mfa.md"},
        follow_redirects=False)
    assert "permalink" in unquote(resp.headers["location"])
    resp = client.post(f"/assessments/{aid}/responses/evidence", data=base | {
        "url": "javascript:alert(1)"}, follow_redirects=False)
    assert "must start with" in unquote(resp.headers["location"])
    links = session.scalars(select(EvidenceLink).order_by(EvidenceLink.id)).all()
    assert [(link.kind, link.pinned) for link in links] == [("repo", True), ("repo", False)]

    client.post(f"/evidence/{links[1].id}/remove")
    session.expire_all()
    assert session.get(EvidenceLink, links[1].id).removed_at is not None  # soft delete


def test_catalog_models_endpoint(client, monkeypatch):
    from app.enrichment import azure_catalog, gcp_catalog
    monkeypatch.setattr(azure_catalog, "list_models", lambda region: {
        "models": [{"name": "gpt-4.1", "format": "OpenAI", "versions": ["2025-04-14"]}]})
    monkeypatch.setattr(gcp_catalog, "list_models", lambda publisher: {
        "models": [{"name": f"{publisher}-model", "versions": []}]})
    data = client.get("/api/catalog-models?csp=azure&region=eastus2,swedencentral").json()
    assert data["models"][0]["name"] == "gpt-4.1"
    data = client.get("/api/catalog-models?csp=gcp&publisher=anthropic").json()
    assert data["models"][0]["name"] == "anthropic-model"


def test_publisher_stored_and_used_by_enrich(client, monkeypatch):
    from app.enrichment import gcp_catalog
    aid = create(client, csp="gcp", service_name="Vertex AI", model_name="claude-sonnet-4-5",
                 publisher="anthropic", model_version="")
    seen = {}
    monkeypatch.setattr(gcp_catalog, "enrich", lambda name, publisher="google": (
        seen.update(publisher=publisher) or {"error": "stub"}))
    client.post(f"/assessments/{aid}/enrich")
    assert seen["publisher"] == "anthropic"


def test_enrich_autofills_and_saves_snapshot(client, session, monkeypatch):
    from app.enrichment import azure_catalog
    monkeypatch.setattr(azure_catalog, "enrich", lambda name, region: {
        "fetched_at": "2026-06-11T00:00:00+00:00", "source": "Azure model catalog (stub)",
        "query": name, "match_count": 1, "models": [{
            "name": "gpt-4.1", "version": "2025-04-14", "lifecycle_status": "GenerallyAvailable",
            "deprecation": {"inference": "2027-01-01T00:00:00Z"}}]})
    aid = create(client)
    save(client, session, aid, "B1", 0, status="fail", narrative="reviewed by hand")
    client.post(f"/assessments/{aid}/enrich")
    assert response(session, aid, "A2").status == "pass"
    assert response(session, aid, "A2").narrative.startswith("[auto]")
    assert response(session, aid, "B2").status == "pass"
    assert response(session, aid, "B1").status == "fail"  # reviewer input untouched
    arts = session.scalars(select(Artifact)).all()
    assert any(a.filename.startswith("catalog-snapshot-") for a in arts)
    a2_links = session.scalars(select(EvidenceLink).where(
        EvidenceLink.response_id == response(session, aid, "A2").id)).all()
    assert a2_links[0].kind == "artifact"
    autofill = session.scalars(select(AuditEvent).where(
        (AuditEvent.action == "autofill") | (AuditEvent.note == "autofill"))).all()
    assert autofill, "auto-fill changes are audited"


def _stub_reference_docs(monkeypatch, pdf_ok=True, hashes_match=True):
    from app.enrichment import reference_docs
    monkeypatch.setattr(reference_docs, "fetch", lambda url: STUB_HTML)
    if pdf_ok:
        monkeypatch.setattr(reference_docs, "fetch_pdf", lambda url: b"%PDF-stub")
    else:
        monkeypatch.setattr(reference_docs, "fetch_pdf",
                            lambda url: (_ for _ in ()).throw(RuntimeError("no chromium")))
    stub_hash = reference_docs.text_hash(STUB_HTML) if hashes_match else "stale"
    for docs in reference_docs.DEFAULT_DOCS.values():
        for d in docs:
            if "validated_hash" in d:
                monkeypatch.setitem(d, "validated_hash", stub_hash)


def _evidence(session, aid, ref):
    r = response(session, aid, ref)
    return session.scalars(select(EvidenceLink).where(EvidenceLink.response_id == r.id)).all() if r else []


def test_reference_docs_saved_and_evidence_linked(client, session, monkeypatch):
    _stub_reference_docs(monkeypatch)
    aid = create(client)
    # pre-existing evidence on C1 must not get a second, automatic link
    client.post(f"/assessments/{aid}/responses/evidence", data={
        "item_id": item_id(session, aid, "C1"), "node_id": node_id(session, aid),
        "url": "https://example.com/my-own-link"})
    resp = client.post(f"/assessments/{aid}/reference-docs", follow_redirects=False)
    assert resp.status_code == 303
    names = {a.filename for a in session.scalars(select(Artifact))}
    assert "azure-openai-data-privacy.pdf" in names
    assert _evidence(session, aid, "C3")[0].artifact.filename == "azure-openai-data-privacy.pdf"
    assert [link.url for link in _evidence(session, aid, "C1")] == ["https://example.com/my-own-link"]

    # curated standard positions auto-fill with provenance
    assert response(session, aid, "C3").status == "pass"
    assert response(session, aid, "C3").narrative.startswith("[auto]")
    assert response(session, aid, "C4").status == "needs_info"  # acceptability stays human
    assert response(session, aid, "E3").status == "pass"
    # items without a position get evidence only
    assert response(session, aid, "C2").status == "unreviewed"
    assert response(session, aid, "E2").status == "unreviewed"

    art = session.scalar(select(Artifact).where(Artifact.filename == "azure-openai-data-privacy.pdf"))
    download = client.get(f"/artifacts/{art.id}")
    assert download.content == b"%PDF-stub"
    assert download.headers["content-type"] == "application/pdf"


def test_reference_docs_changed_page_blocks_positions(client, session, monkeypatch):
    _stub_reference_docs(monkeypatch, hashes_match=False)
    aid = create(client)
    resp = client.post(f"/assessments/{aid}/reference-docs", follow_redirects=False)
    assert "changed" in unquote(resp.headers["location"])
    assert response(session, aid, "C3").status == "needs_info"
    assert "changed" in response(session, aid, "C3").narrative
    assert _evidence(session, aid, "C3")


def test_reference_docs_html_fallback_when_pdf_fails(client, session, monkeypatch):
    _stub_reference_docs(monkeypatch, pdf_ok=False)
    aid = create(client)
    client.post(f"/assessments/{aid}/reference-docs")
    names = {a.filename for a in session.scalars(select(Artifact))}
    assert "azure-openai-data-privacy.html" in names
    assert response(session, aid, "C3").status == "pass"  # positions unaffected by pdf failure


def test_settings_admin_only_and_escaped(client):
    resp = client.post("/settings/reference-docs/add", data={
        "csp": "internal", "title": "x');alert(1);('", "url": "https://intranet.example.com/p",
        "items": "C1"}, follow_redirects=False)
    assert resp.status_code == 303
    page = client.get("/settings")
    assert "alert(1)" in page.text and "confirm('x');alert" not in page.text
    assert 'data-confirm="Remove x&#39;);alert(1);(&#39; from this list?"' in page.text
    as_user(client, REVIEWER)
    assert client.get("/settings").status_code == 403
    assert client.get("/api/check-url?url=https://example.com").status_code == 403


def test_export_json(client, session):
    aid = create(client)
    save(client, session, aid, "A1", 0, status="pass", narrative="ok")
    data = client.get(f"/assessments/{aid}/export.json").json()
    assert data["context"]["model_name"] == "gpt-4.1"
    a1 = next(i for i in data["items"] if i["ref"] == "A1")
    assert a1["status"] == "pass" and a1["updated_by"] == "alice@dev.local"
    assert len(data["items"]) > 40
