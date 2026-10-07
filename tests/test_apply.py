"""Unit tests for mapping catalog snapshots onto checklist items."""
from app import checklist
from app.enrichment import apply


def make_doc(model_name="gpt-4.1", model_version="", csp="azure"):
    return {"csp": csp, "model_name": model_name, "model_version": model_version,
            "checklist": checklist.new_checklist()}


def azure_snapshot(models):
    return {"source": "Azure model catalog (eastus2)", "models": models}


GA_MODEL = {"name": "gpt-4.1", "version": "2025-04-14",
            "lifecycle_status": "GenerallyAvailable",
            "deprecation": {"inference": "2026-10-14T00:00:00Z"}}


def test_azure_ga_model_fills_a2_b1_b2():
    doc = make_doc()
    updates = apply.apply_azure(azure_snapshot([GA_MODEL]), doc)
    assert {u["item"] for u in updates} == {"A2", "B1", "B2"}
    assert doc["checklist"]["A2"]["status"] == "pass"
    assert doc["checklist"]["B1"]["status"] == "pass"
    assert doc["checklist"]["B2"]["status"] == "pass"
    assert "2026-10-14" in doc["checklist"]["B2"]["notes"]
    assert doc["checklist"]["A2"]["notes"].startswith("[auto]")


def test_azure_preview_model_needs_info():
    preview = dict(GA_MODEL, lifecycle_status="Preview", deprecation=None)
    doc = make_doc()
    apply.apply_azure(azure_snapshot([preview]), doc)
    assert doc["checklist"]["B1"]["status"] == "needs_info"
    assert doc["checklist"]["B2"]["status"] == "unreviewed"


def test_azure_no_match_flags_a2():
    doc = make_doc(model_name="gpt-9000")
    apply.apply_azure(azure_snapshot([GA_MODEL]), doc)
    assert doc["checklist"]["A2"]["status"] == "needs_info"
    assert doc["checklist"]["B1"]["status"] == "unreviewed"


def test_azure_multiple_versions_ask_to_pin():
    v2 = dict(GA_MODEL, version="2025-06-01")
    doc = make_doc()
    apply.apply_azure(azure_snapshot([GA_MODEL, v2]), doc)
    assert doc["checklist"]["A2"]["status"] == "needs_info"
    assert "pin one" in doc["checklist"]["A2"]["notes"]


def test_azure_pinned_version_filters():
    v2 = dict(GA_MODEL, version="2025-06-01")
    doc = make_doc(model_version="2025-04-14")
    apply.apply_azure(azure_snapshot([GA_MODEL, v2]), doc)
    assert doc["checklist"]["A2"]["status"] == "pass"
    assert "2025-04-14" in doc["checklist"]["A2"]["notes"]


def test_never_overwrites_reviewer_input():
    doc = make_doc()
    doc["checklist"]["B1"] = {"status": "fail", "notes": "reviewed by hand", "evidence": ""}
    updates = apply.apply_azure(azure_snapshot([GA_MODEL]), doc)
    assert doc["checklist"]["B1"]["status"] == "fail"
    assert doc["checklist"]["B1"]["notes"] == "reviewed by hand"
    assert {u["item"] for u in updates} == {"A2", "B2"}


def test_gcp_ga_fills_a2_b1():
    doc = make_doc(model_name="gemini-2.5-flash", csp="gcp")
    snapshot = {"source": "Vertex AI publisher catalog",
                "model": {"name": "publishers/google/models/gemini-2.5-flash",
                          "version_id": "001", "launch_stage": "GA"}}
    updates = apply.apply_gcp(snapshot, doc)
    assert {u["item"] for u in updates} == {"A2", "B1"}
    assert doc["checklist"]["B1"]["status"] == "pass"


def test_gcp_preview_needs_info():
    doc = make_doc(csp="gcp")
    snapshot = {"source": "Vertex AI publisher catalog",
                "model": {"name": "publishers/google/models/x",
                          "version_id": "001", "launch_stage": "PUBLIC_PREVIEW"}}
    apply.apply_gcp(snapshot, doc)
    assert doc["checklist"]["B1"]["status"] == "needs_info"


def test_never_overwrites_notes_typed_on_unreviewed_item():
    doc = make_doc()
    doc["checklist"]["A2"]["notes"] = "checking the id with the vendor"
    updates = apply.apply_azure(azure_snapshot([GA_MODEL]), doc)
    assert doc["checklist"]["A2"]["notes"] == "checking the id with the vendor"
    assert "A2" not in {u["item"] for u in updates}
