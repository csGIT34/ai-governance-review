"""Pinning evidence-repo links through the GitHub API (stubbed)."""
import httpx
import pytest
from sqlalchemy import select

from app import config
from app.models import EvidenceLink
from app.services import github
from conftest import item_id, node_id
from test_ai_review import create

SHA = "0123456789abcdef0123456789abcdef01234567"


@pytest.fixture
def api(monkeypatch):
    """Fake GitHub Enterprise: branch main and release/2026-q4 exist; one file exists."""
    monkeypatch.setattr(config, "GITHUB_TOKEN", "t")
    monkeypatch.setattr(config, "GITHUB_API_URL", "https://ghe.corp/api/v3")
    calls = []

    def fake_get(path):
        calls.append(path)
        if path.startswith("/repos/org/audit/commits/"):
            ref = path.rsplit("/commits/", 1)[1]
            if ref in ("main", "release/2026-q4", SHA):
                return 200, {"sha": SHA}
            return 422 if "/" in ref else 404, {}
        if path.startswith(f"/repos/org/audit/contents/evidence/mfa.md?ref={SHA}"):
            return 200, {}
        return 404, {}
    monkeypatch.setattr(github, "_get", fake_get)
    return calls


def test_branch_link_pinned(api):
    p = github.pin("https://ghe.corp/org/audit/blob/main/evidence/mfa.md#L10")
    assert p.pinned and p.url == f"https://ghe.corp/org/audit/blob/{SHA}/evidence/mfa.md#L10"
    assert "Pinned 'main'" in p.message


def test_branch_with_slash(api):
    p = github.pin("https://ghe.corp/org/audit/blob/release/2026-q4/evidence/mfa.md")
    assert p.pinned and SHA in p.url


def test_already_pinned_is_checked_not_rewritten(api):
    url = f"https://ghe.corp/org/audit/blob/{SHA}/evidence/mfa.md"
    p = github.pin(url)
    assert (p.url, p.pinned, p.message) == (url, True, "")


def test_missing_file_flagged(api):
    p = github.pin("https://ghe.corp/org/audit/blob/main/evidence/typo.md")
    assert p.pinned is False and "doesn't exist" in p.message


def test_api_down_keeps_link(api, monkeypatch):
    def boom(path):
        raise httpx.ConnectError("down")
    monkeypatch.setattr(github, "_get", boom)
    url = "https://ghe.corp/org/audit/blob/main/evidence/mfa.md"
    p = github.pin(url)
    assert (p.url, p.pinned) == (url, False) and "unreachable" in p.message


def test_other_hosts_and_no_token_untouched(api, monkeypatch):
    assert github.pin("https://github.com/org/audit/blob/main/x.md").pinned is None
    monkeypatch.setattr(config, "GITHUB_TOKEN", "")
    assert github.pin("https://ghe.corp/org/audit/blob/main/evidence/mfa.md").pinned is None


def test_pinning_through_the_app(client, session, api):
    aid = create(client)
    resp = client.post(f"/assessments/{aid}/responses/evidence", data={
        "item_id": item_id(session, aid, "A1"), "node_id": node_id(session, aid),
        "url": "https://ghe.corp/org/audit/blob/main/evidence/mfa.md"}, follow_redirects=False)
    assert "Pinned" in resp.headers["location"].replace("+", " ")
    link = session.scalar(select(EvidenceLink))
    assert link.pinned and SHA in link.url
