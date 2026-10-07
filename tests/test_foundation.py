"""Auth, CSRF, SSRF guard, admin guards, and the Postgres append-only trigger."""
import base64
import json
import os

import pytest
from sqlalchemy import select, text

from app import config, db
from app.enrichment import reference_docs
from app.models import AuditEvent, User
from conftest import ADMIN, REVIEWER, as_user


def test_easyauth_requires_identity(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "easyauth")
    assert client.get("/", headers={"accept": "application/json"}).status_code == 401
    principal = base64.b64encode(json.dumps(
        {"claims": [{"typ": "name", "val": "Dana Doe"}]}).encode()).decode()
    resp = client.get("/", headers={"x-ms-client-principal-name": "Dana@Corp.com",
                                    "x-ms-client-principal": principal})
    assert resp.status_code == 200
    with db.SessionLocal() as s:
        dana = s.scalar(select(User).where(User.upn == "dana@corp.com"))
        assert (dana.display_name, dana.role) == ("Dana Doe", config.DEFAULT_ROLE)


def test_easyauth_bootstrap_admin(client, monkeypatch):
    monkeypatch.setattr(config, "AUTH_MODE", "easyauth")
    monkeypatch.setattr(config, "ADMIN_UPNS", {"boss@corp.com"})
    client.get("/", headers={"x-ms-client-principal-name": "boss@corp.com"})
    with db.SessionLocal() as s:
        assert s.scalar(select(User.role).where(User.upn == "boss@corp.com")) == "admin"


def test_inactive_user_blocked(client, session):
    as_user(client, REVIEWER)
    client.get("/")
    bob = session.scalar(select(User).where(User.upn == REVIEWER))
    as_user(client, ADMIN)
    client.post(f"/admin/users/{bob.id}", data={"role": "reviewer"})  # active unchecked
    as_user(client, REVIEWER)
    assert client.get("/").status_code == 403


def test_last_admin_cannot_be_removed(client, session):
    client.get("/")
    alice = session.scalar(select(User).where(User.upn == ADMIN))
    resp = client.post(f"/admin/users/{alice.id}", data={"role": "viewer", "active": "on"},
                       follow_redirects=False)
    assert "last active admin" in resp.headers["location"].replace("+", " ")
    session.expire_all()
    assert session.get(User, alice.id).role == "admin"


def test_cross_site_posts_blocked(client):
    resp = client.post("/assessments/ai", data={"csp": "azure"},
                       headers={"sec-fetch-site": "cross-site"})
    assert resp.status_code == 403
    resp = client.post("/assessments/ai", data={"csp": "azure"},
                       headers={"origin": "https://evil.example"})
    assert resp.status_code == 403


@pytest.mark.parametrize("url", ["http://169.254.169.254/metadata", "http://127.0.0.1:8000/",
                                 "http://10.1.2.3/", "file:///etc/passwd", "http://[::1]/"])
def test_ssrf_guard_blocks_internal_targets(url):
    with pytest.raises(ValueError):
        reference_docs.check_public_url(url)


@pytest.mark.skipif(not os.environ["TEST_DATABASE_URL"].startswith("postgresql"),
                    reason="trigger is Postgres-only")
def test_audit_log_is_append_only_on_postgres():
    """Runs the real migrations into a scratch schema and checks the trigger."""
    from alembic import command
    from alembic.config import Config
    engine = db.engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS migtest CASCADE; CREATE SCHEMA migtest"))
    cfg = Config("alembic.ini")
    url = (os.environ["TEST_DATABASE_URL"] + ("&" if "?" in os.environ["TEST_DATABASE_URL"] else "?")
           + "options=-csearch_path%3Dmigtest")
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # configparser escaping
    command.upgrade(cfg, "head")
    with engine.begin() as conn:
        conn.execute(text("SET search_path TO migtest"))
        conn.execute(text("INSERT INTO audit_events (at, actor, action, entity_type, entity_id, "
                          "changes, note) VALUES (now(), 'x', 'create', 't', '1', '{}', '')"))
    for stmt in ("UPDATE audit_events SET actor = 'y'", "DELETE FROM audit_events",
                 "TRUNCATE audit_events"):
        with pytest.raises(Exception, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text("SET search_path TO migtest"))
                conn.execute(text(stmt))
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA migtest CASCADE"))
    assert AuditEvent.__tablename__ == "audit_events"
