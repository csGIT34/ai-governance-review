"""Browser test of the response editor's autosave (static/app.js) against a live server.

Skipped unless Playwright's Chromium is installed (`playwright install chromium`).
"""
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from app import db, seed
from app.models import ScopeNode

sync_api = pytest.importorskip("playwright.sync_api")


@pytest.fixture
def server(tmp_path):
    engine = db.init_engine(f"sqlite:///{tmp_path / 'e2e.db'}")
    db.Base.metadata.create_all(engine)
    from app import main
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = uvicorn.Server(uvicorn.Config(main.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    thread.join(5)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as err:
            pytest.skip(f"Chromium not available: {err}")
        yield b
        b.close()


def _assessment(base: str) -> tuple[str, str]:
    """Create library + scope + assessment; return (item page url for mg-prod, assessment id)."""
    with db.SessionLocal() as s:
        seed.load_demo(s)
        s.commit()
        root = s.scalar(select(ScopeNode).where(ScopeNode.kind == "tenant")).id
        lib = s.scalar(select(seed.Library.id).where(seed.Library.key == "demo-cloud-controls"))
    c = httpx.Client(base_url=base, cookies={"dev_user": "alice@dev.local"})
    r = c.post("/assessments/controls", data={"name": "E2E", "library_id": lib, "root_id": root})
    aid = r.headers["location"].split("?")[0].rsplit("/", 1)[-1]
    page = c.get(f"/assessments/{aid}").text
    item = page.split(f"/assessments/{aid}/items/")[1].split('"')[0]
    item_page = c.get(f"/assessments/{aid}/items/{item}").text
    mg = item_page.split("?node=")[2].split('"')[0]  # [1] is the tenant root, [2] the first MG
    return f"{base}/assessments/{aid}/items/{item}?node={mg}", aid


def _page(browser, base, upn):
    ctx = browser.new_context()
    ctx.add_cookies([{"name": "dev_user", "value": upn, "url": base}])
    return ctx.new_page()


def test_autosave_conflict_and_flush(server, browser):
    url, _ = _assessment(server)
    narrative = 'textarea[name="narrative"]'

    carol = _page(browser, server, "carol@dev.local")
    carol.goto(url)
    carol.fill(narrative, "Enforced by Azure Policy at mg level.")
    carol.locator(".save-state").filter(has_text="Saved").wait_for(timeout=5000)
    carol.reload()
    assert carol.input_value(narrative) == "Enforced by Azure Policy at mg level."

    # Two editors on the same answer: the second save is rejected, the text survives.
    alice = _page(browser, server, "alice@dev.local")
    alice.goto(url)
    carol.fill(narrative, "Carol's newer wording")
    carol.locator(".save-state").filter(has_text="Saved").wait_for(timeout=5000)
    alice.fill(narrative, "Alice's competing wording")
    alice.locator(".save-state").filter(has_text="Not saved").wait_for(timeout=5000)
    assert alice.input_value(narrative) == "Alice's competing wording"
    carol.reload()
    assert carol.input_value(narrative) == "Carol's newer wording"

    # Typing then immediately submitting another form: autosave is flushed first.
    carol.fill(narrative, "Text typed right before adding a link")
    carol.fill('input[name="url"]', "https://github.example.com/org/audit/blob/"
                                    "0123456789abcdef0123456789abcdef01234567/mfa.md")
    carol.click("text=Add link")
    carol.wait_for_load_state("load")
    assert carol.input_value(narrative) == "Text typed right before adding a link"
    assert carol.locator("text=pinned").count() >= 1
