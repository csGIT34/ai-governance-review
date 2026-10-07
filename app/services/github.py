"""Pin evidence-repo links to a commit through the GitHub / GitHub Enterprise REST API.

With GITHUB_TOKEN set, a link to a file on a branch (.../blob/main/evidence/x.md) is
rewritten to the commit the branch points at right now (.../blob/<sha>/evidence/x.md), so the
evidence can't change underneath the answer, and the file is checked to exist at that commit.
Without a token, or if the API can't be reached, the link is kept as typed and flagged
"not pinned" (see evidence.classify).
"""
import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from app import config

FILE_LINK = re.compile(r"^(https?://[^/]+)/([^/]+)/([^/]+)/(blob|tree|raw)/([^?#]+)([?#].*)?$")


@dataclass
class Pin:
    url: str
    pinned: bool | None  # None: not a link this module handles
    message: str = ""


def enabled() -> bool:
    return bool(config.GITHUB_TOKEN)


def api_url() -> str:
    return (config.GITHUB_API_URL or "https://api.github.com").rstrip("/")


def web_host() -> str:
    """Host of the web UI for the configured API (api.github.com -> github.com;
    https://ghe.corp/api/v3 -> ghe.corp)."""
    host = urlparse(api_url()).hostname or ""
    return "github.com" if host == "api.github.com" else host


def _get(path: str) -> tuple[int, dict]:
    resp = httpx.get(f"{api_url()}{path}", timeout=10, headers={
        "Authorization": f"Bearer {config.GITHUB_TOKEN}", "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"})
    try:
        body = resp.json()
    except ValueError:
        body = {}
    return resp.status_code, body if isinstance(body, dict) else {"items": body}


def pin(url: str) -> Pin:
    m = FILE_LINK.match(url.strip())
    if not enabled() or not m or urlparse(m.group(1)).hostname != web_host():
        return Pin(url, None)
    origin, owner, repo, mode, rest, suffix = m.groups()
    segments = rest.strip("/").split("/")
    try:
        # The ref may itself contain slashes (release/2026-q4): try the shortest first.
        for cut in range(1, min(len(segments), 5)):
            ref, path = "/".join(segments[:cut]), "/".join(segments[cut:])
            status, body = _get(f"/repos/{owner}/{repo}/commits/{quote(ref, safe='/')}")
            if status == 200 and body.get("sha"):
                sha = body["sha"]
                break
            if status not in (404, 422):
                return Pin(url, False, f"GitHub API returned {status}; link added without pinning.")
        else:
            return Pin(url, False, "Couldn't find that branch or commit in the repo - check the link.")
        status, _ = _get(f"/repos/{owner}/{repo}/contents/{quote(path)}?ref={sha}")
        if status != 200:
            return Pin(url, False, f"{path} doesn't exist at commit {sha[:7]} - check the link.")
    except httpx.HTTPError as err:
        return Pin(url, False, f"GitHub API unreachable ({err.__class__.__name__}); link added "
                               "without pinning.")
    pinned_url = f"{origin}/{owner}/{repo}/{mode}/{sha}/{path}{suffix or ''}"
    return Pin(pinned_url, True, "" if ref == sha else f"Pinned '{ref}' to commit {sha[:7]}.")
