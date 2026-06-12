"""Dated snapshots of CSP data-privacy / terms pages.

These agreements are per-CSP, not per-model, so every review pulls the same
small set. Each page is rendered to PDF (headless Chromium; falls back to the
raw HTML if rendering fails) and stored as a review artifact, and the artifact
name is written into the evidence field of the related checklist items that
are still unreviewed.

The doc list (per-CSP URLs, item mappings, positions) is editable on the
/settings page; edits are stored in Cosmos and take precedence. The bundled
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
import json
import re

import httpx

from app import config
from app.db import cosmos

with open(config.REFERENCE_DOCS_PATH, encoding="utf-8") as _f:
    DEFAULT_DOCS = json.load(_f)


def get_docs() -> dict:
    """Effective doc list: the version edited on the settings page (stored
    in Cosmos) if present, otherwise the bundled defaults."""
    override = cosmos.get_settings("reference-docs")
    return override["docs"] if override else DEFAULT_DOCS

CHANGED_NOTE = ("this source document has changed since its standard position was "
                "validated - re-review the snapshot and re-validate the position "
                "(see reference_docs.json).")


def fetch(url: str) -> bytes:
    resp = httpx.get(url, follow_redirects=True, timeout=30,
                     headers={"User-Agent": "Mozilla/5.0 (AI-Governance-Review)"})
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
    with sync_playwright() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
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
    for _csp, _docs in get_docs().items():
        if _csp == "internal":
            continue
        for _d in _docs:
            try:
                _h = text_hash(fetch(_d["url"]))
            except Exception as _err:
                _h = f"ERROR: {_err}"
            print(f'{_csp:5} {_d["slug"]:40} {_h}')
