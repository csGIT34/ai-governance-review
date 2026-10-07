"""Teams notifications (webhook stubbed)."""
from datetime import date

import pytest
from sqlalchemy import select

from app import config, jobs, notify
from app.models import Issue
from conftest import PREPARER, REVIEWER, as_user, item_id, node_id
from test_controls import COMPLETE, answer, setup, transition  # noqa: F401  (fixture)


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(config, "TEAMS_WEBHOOK_URL", "https://teams.example/webhook")
    monkeypatch.setattr(config, "APP_BASE_URL", "https://gov.corp")
    monkeypatch.setattr(notify, "_post", out.append)
    return out


def _title(payload):
    return payload["attachments"][0]["content"]["body"][0]["text"]


def test_prepare_and_return_notify(client, session, setup, sent):
    aid, root = setup["aid"], "Contoso tenant (demo)"
    as_user(client, PREPARER)
    answer(client, session, aid, "IAM-01", root, **COMPLETE)
    transition(client, session, aid, "IAM-01", root, "prepare")
    as_user(client, REVIEWER)
    transition(client, session, aid, "IAM-01", root, "return", "attach the CA export")
    assert [_title(p) for p in sent] == [
        "Ready for review: IAM-01 at Contoso tenant (demo)",
        f"Returned to {PREPARER}: IAM-01 at Contoso tenant (demo)"]
    action = sent[1]["attachments"][0]["content"]["actions"][0]
    assert action["url"].startswith("https://gov.corp/assessments/")


def test_failures_never_break_requests(client, session, setup, monkeypatch):
    monkeypatch.setattr(config, "TEAMS_WEBHOOK_URL", "https://teams.example/webhook")

    def down(payload):
        raise ConnectionError("teams down")
    monkeypatch.setattr(notify, "_post", down)
    aid = setup["aid"]
    resp = client.post(f"/assessments/{aid}/issues", data={
        "item_id": item_id(session, aid, "IAM-01"), "node_id": node_id(session, aid),
        "title": "critical gap", "severity": "critical"}, follow_redirects=False)
    assert resp.status_code == 303 and session.scalar(select(Issue)) is not None


def test_only_high_issues_notify_and_disabled_without_url(client, session, setup, sent, monkeypatch):
    aid = setup["aid"]
    for sev in ("low", "high"):
        client.post(f"/assessments/{aid}/issues", data={
            "item_id": item_id(session, aid, "IAM-01"), "node_id": node_id(session, aid),
            "title": f"{sev} gap", "severity": sev})
    assert [_title(p) for p in sent] == ["High issue raised: ISS-0002 high gap"]
    monkeypatch.setattr(config, "TEAMS_WEBHOOK_URL", "")
    notify.send("nothing")
    assert len(sent) == 1


def test_due_issue_digest(client, session, setup, sent):
    aid = setup["aid"]
    for title, due in (("overdue", "2026-10-01"), ("soon", "2026-10-10"), ("later", "2026-12-01")):
        client.post(f"/assessments/{aid}/issues", data={
            "item_id": item_id(session, aid, "IAM-01"), "node_id": node_id(session, aid),
            "title": title, "severity": "low", "due_date": due})
    assert jobs.notify_due_issues(session, today=date(2026, 10, 6)) == 2
    assert _title(sent[0]) == "2 issue(s) due within 7 days (1 overdue)"
