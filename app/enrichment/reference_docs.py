"""Dated snapshots of CSP data-privacy / terms pages.

These agreements are per-CSP, not per-model, so every review pulls the same
small set. Each page is rendered to PDF (headless Chromium; falls back to the
raw HTML if rendering fails) and stored as a review artifact, and the artifact
name is written into the evidence field of the related checklist items that
are still unreviewed.

The doc list (per-CSP URLs, item mappings, positions) is editable on the
/settings page (admins); edits are stored in the settings table and take precedence. The bundled
defaults live in reference_docs.json next to this module (path overridable
via REFERENCE_DOCS_PATH).

A doc may also carry "positions": curated standard answers for items whose
outcome is a stable CSP-level fact rather than a per-review judgment. These
auto-fill status and an [auto] note, but only on items that are still
unreviewed with empty notes. Use-case-dependent items (acceptability for a
data classification, SLA coverage of a specific feature) deliberately get
needs_info or evidence-only, so a human still decides.

Position drift protection: every doc with positions records validated_hash,
a sha256 of the page's visible text at the time the positions were curated.
If the live page's text no longer matches, the positions are NOT applied and
the items are flagged needs_info instead. To re-validate after reviewing a
changed page, run:  python -m app.enrichment.reference_docs  and paste the
printed hashes into reference_docs.json.
"""
import hashlib
import ipaddress
import json
import re
import socket
from urllib.parse import urlparse

import httpx
from sqlalchemy.orm import Session

from app import config
from app.models import Setting

with open(config.REFERENCE_DOCS_PATH, encoding="utf-8") as _f:
    DEFAULT_DOCS = json.load(_f)

SETTING_KEY = "reference-docs"


def get_docs(db: Session) -> dict:
    """Effective doc list: the version edited on the settings page (stored
    in the settings table) if present, otherwise the bundled defaults."""
    override = db.get(Setting, SETTING_KEY)
    return override.value["docs"] if override else DEFAULT_DOCS


def check_public_url(url: str):
    """Refuse URLs the server must not fetch on a user's behalf (SSRF): non-http(s)
    schemes and hosts resolving to private, loopback, link-local (e.g. the cloud
    metadata endpoint 169.254.169.254) or otherwise non-public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("URL must start with http:// or https://")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or 443)
    except socket.gaierror as err:
        raise ValueError(f"can't resolve {parsed.hostname}: {err}") from err
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ValueError(f"{parsed.hostname} is an internal address - the server won't "
                             "fetch it; open it in your browser instead")

CHANGED_NOTE = ("this source document has changed since its standard position was "
                "validated - re-review the snapshot and re-validate the position "
                "(see reference_docs.json).")


def fetch(url: str) -> bytes:
    check_public_url(url)
    with httpx.Client(timeout=30, headers={"User-Agent": "Mozilla/5.0 (AI-Governance-Review)"}) as client:
        resp = client.get(url)
        for _ in range(5):  # follow redirects by hand so each hop is checked
            if not resp.is_redirect:
                break
            url = str(resp.next_request.url)
            check_public_url(url)
            resp = client.get(url)
    resp.raise_for_status()
    return resp.content


def text_hash(content: bytes) -> str:
    """sha256 of the page's visible text, so markup/script churn doesn't count
    as a terms change but any visible wording change does."""
    html = content.decode("utf-8", errors="ignore")
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    html = re.sub(r"(?s)<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", html).strip()
    return hashlib.sha256(text.encode()).hexdigest()


def fetch_pdf(url: str) -> bytes:
    """Print the rendered page to PDF with headless Chromium."""
    from playwright.sync_api import sync_playwright
    check_public_url(url)
    allowed: dict[str, bool] = {}

    def guard(route):
        """Every request the page makes (redirects, subresources, iframes, fetch) must
        pass the same SSRF check; checked once per host."""
        host = urlparse(route.request.url).hostname or ""
        if host not in allowed:
            try:
                check_public_url(route.request.url)
                allowed[host] = True
            except ValueError:
                allowed[host] = False
        return route.continue_() if allowed[host] else route.abort()

    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.route("**/*", guard)
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass  # print whatever has rendered
            return page.pdf(format="A4", print_background=True)
        finally:
            browser.close()


if __name__ == "__main__":
    # Print current text hashes for pasting into validated_hash after a re-review.
    # Internal docs are skipped: they are linked, never snapshotted/validated.
    # Uses the bundled defaults (reference_docs.json), not settings-page edits.
    for _csp, _docs in DEFAULT_DOCS.items():
        if _csp == "internal":
            continue
        for _d in _docs:
            try:
                _h = text_hash(fetch(_d["url"]))
            except Exception as _err:
                _h = f"ERROR: {_err}"
            print(f'{_csp:5} {_d["slug"]:40} {_h}')
