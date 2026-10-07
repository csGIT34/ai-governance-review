"""Azure Resource Graph evidence (API stubbed)."""
import json
from urllib.parse import unquote_plus as unquote

import pytest
from sqlalchemy import select

from app.models import Artifact, EvidenceLink, LibraryItem
from app.services import azure_evidence, scope
from conftest import PREPARER, as_user, item_id, node_id
from test_controls import COMPLETE, answer, setup  # noqa: F401  (fixture)

QUERY = "securityresources | where type == 'microsoft.security/pricings'"


@pytest.fixture
def graph(monkeypatch):
    calls = []

    class Resp:
        def __init__(self, body, status=200):
            self.status_code, self._body = status, body
            self.headers = {"content-type": "application/json"}
            self.text = json.dumps(body)

        def json(self):
            return self._body

    def post(url, params, headers, timeout, json):
        calls.append(json)
        if "$skipToken" not in json["options"]:
            return Resp({"data": [{"subscriptionId": s, "tier": "Standard"} for s in json["subscriptions"]],
                         "totalRecords": 2 * len(json["subscriptions"]), "$skipToken": "page2"})
        return Resp({"data": [{"subscriptionId": "extra", "tier": "Free"}] * len(json["subscriptions"]),
                     "totalRecords": 2 * len(json["subscriptions"])})
    monkeypatch.setattr(scope, "_token", lambda: "tok")
    monkeypatch.setattr(azure_evidence.httpx, "post", post)
    return calls


def _with_query(session, setup):
    lib_item = session.scalar(select(LibraryItem).where(LibraryItem.library_id == setup["lid"],
                                                        LibraryItem.ref == "IAM-01"))
    return lib_item


def test_query_result_attached_as_evidence(client, session, setup, graph):
    aid = setup["aid"]
    iid = item_id(session, aid, "IAM-01")
    # the assessment snapshot holds the query; set it there for this test
    from app.models import AssessmentItem
    session.get(AssessmentItem, iid).evidence_query = QUERY
    session.commit()
    answer(client, session, aid, "IAM-01", "mg-prod", **COMPLETE)
    as_user(client, PREPARER)
    resp = client.post(f"/assessments/{aid}/items/{iid}/azure-evidence",
                       data={"node_id": node_id(session, aid, "mg-prod")}, follow_redirects=False)
    assert "Attached: Azure Resource Graph: 4 rows across 2 subscriptions" in unquote(resp.headers["location"])
    assert graph[0]["subscriptions"] == ["00000000-0000-0000-0000-000000000003",
                                         "00000000-0000-0000-0000-000000000004"]  # only under mg-prod
    art = session.scalar(select(Artifact))
    payload = json.loads(client.get(f"/artifacts/{art.id}").content)
    assert payload["query"] == QUERY and payload["total_records"] == 4 and len(payload["rows"]) == 4
    assert session.scalar(select(EvidenceLink)).artifact_id == art.id


def test_no_query_or_inheriting_node_refused(client, session, setup, graph):
    aid = setup["aid"]
    iid = item_id(session, aid, "IAM-01")
    resp = client.post(f"/assessments/{aid}/items/{iid}/azure-evidence",
                       data={"node_id": node_id(session, aid)}, follow_redirects=False)
    assert "no Azure evidence query" in unquote(resp.headers["location"])
    from app.models import AssessmentItem
    session.get(AssessmentItem, iid).evidence_query = QUERY
    session.commit()
    answer(client, session, aid, "IAM-01", "mg-prod", **COMPLETE)
    resp = client.post(f"/assessments/{aid}/items/{iid}/azure-evidence",
                       data={"node_id": node_id(session, aid, "sub-app-a-prod")}, follow_redirects=False)
    assert "inherits its answer" in unquote(resp.headers["location"])
    assert not graph  # refused before calling Azure


def test_query_column_imports(client, session, setup):
    client.post(f"/libraries/{setup['lid']}/import", data={
        "text": "ref\ttitle\tKQL\nIAM-01\tMFA for privileged access\tresources | take 1\n"})
    assert _with_query(session, setup).evidence_query == "resources | take 1"



def test_truncation_detected_without_paging(monkeypatch):
    """No `id` projected -> Resource Graph returns one page, no $skipToken, resultTruncated."""
    class Resp:
        status_code = 200
        headers = {"content-type": "application/json"}

        def json(self):
            return {"data": [{"x": 1}] * 1000, "totalRecords": 2500, "resultTruncated": "true"}
    monkeypatch.setattr(scope, "_token", lambda: "tok")
    monkeypatch.setattr(azure_evidence.httpx, "post", lambda *a, **k: Resp())
    result = azure_evidence.run_query(["sub"], "resources")
    assert len(result["rows"]) == 1000 and result["truncated"] is True
