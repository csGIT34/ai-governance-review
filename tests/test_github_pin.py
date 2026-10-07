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



def test_encoded_paths_not_double_encoded(api, monkeypatch):
    calls = []
    def fake(path):
        calls.append(path)
        return (200, {"sha": SHA}) if "/commits/" in path else (200, {})
    monkeypatch.setattr(github, "_get", fake)
    p = github.pin("https://ghe.corp/org/audit/blob/main/evidence/Access%20Review.md")
    assert p.pinned and calls[1].startswith("/repos/org/audit/contents/evidence/Access%20Review.md")
    assert p.url.endswith(f"/blob/{SHA}/evidence/Access%20Review.md")


def test_dot_segments_never_reach_the_api(api):
    p = github.pin("https://ghe.corp/org/audit/blob/main/../../../../user")
    assert p.pinned is False and "invalid path" in p.message and not api


def test_commit_link_stays_pinned_when_api_fails(api, monkeypatch):
    from app.services import evidence
    monkeypatch.setattr(github, "_get", lambda path: (503, {}))
    r = evidence.resolve(f"https://ghe.corp/org/audit/blob/{SHA}/evidence/mfa.md")
    assert r.pinned is True and r.problem and "couldn't confirm" in r.message


def test_only_evidence_repo_links_go_to_the_api(api, monkeypatch):
    from app.services import evidence
    monkeypatch.setattr(config, "EVIDENCE_REPO_BASE", "https://ghe.corp/org/audit")
    r = evidence.resolve("https://ghe.corp/org/other-repo/blob/main/x.md")
    assert r.pinned is False and not api  # flagged as a branch link, GitHub not called
