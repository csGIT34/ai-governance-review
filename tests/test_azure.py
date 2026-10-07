"""Azure wiring: passwordless Postgres, managed-identity blob auth, readiness probe."""
import os

import pytest
from sqlalchemy import text

from app import blob, config, db


class StubCredential:
    def __init__(self, token="entra-token"):
        self.token, self.scopes = token, []

    def get_token(self, *scopes):
        self.scopes += scopes
        return type("T", (), {"token": self.token})()


def _postgres_url(user_pw_free: bool) -> str:
    url = os.environ["TEST_DATABASE_URL"]
    return url.replace(":governance@", "@") if user_pw_free else url


def test_entra_token_is_used_as_postgres_password(monkeypatch):
    stub = StubCredential()
    monkeypatch.setattr(config, "DATABASE_AUTH", "entra")
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: stub)
    eng = db.build_engine("postgresql+psycopg://app-identity@db.example:5432/governance")
    seen = {}

    @db.event.listens_for(eng, "do_connect")
    def capture(dialect, conn_rec, cargs, cparams):  # runs after the app's listener
        seen.update(cparams)
        raise RuntimeError("stop before a real connection")

    with pytest.raises(Exception, match="stop before"):
        eng.connect()
    assert seen["password"] == "entra-token" and seen["user"] == "app-identity"
    assert stub.scopes == [db.POSTGRES_ENTRA_SCOPE]


@pytest.mark.skipif(not os.environ["TEST_DATABASE_URL"].startswith("postgresql"),
                    reason="needs Postgres")
def test_entra_mode_connects_for_real_with_token_as_password(monkeypatch):
    """Against the test Postgres: the 'token' is the real password, the URL has none."""
    monkeypatch.setattr(config, "DATABASE_AUTH", "entra")
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: StubCredential("governance"))
    eng = db.build_engine(_postgres_url(user_pw_free=True))
    with eng.connect() as conn:
        assert conn.execute(text("SELECT 1")).scalar() == 1
    eng.dispose()


def test_blob_uses_managed_identity_when_account_url_set(monkeypatch):
    stub = StubCredential()
    monkeypatch.setattr(config, "BLOB_ACCOUNT_URL", "https://acct.blob.core.windows.net")
    monkeypatch.setattr("azure.identity.DefaultAzureCredential", lambda: stub)
    client = blob._client()
    assert client.url.startswith("https://acct.blob.core.windows.net")
    assert client.credential is stub


def test_blob_uses_connection_string_by_default(monkeypatch):
    monkeypatch.setattr(config, "BLOB_ACCOUNT_URL", "")
    assert blob._client().account_name == "devstoreaccount1"


def test_readiness_probe(client, monkeypatch):
    assert client.get("/health/ready").json() == {"status": "ok"}
    broken = db.build_engine("postgresql+psycopg://x:y@127.0.0.1:1/none")
    monkeypatch.setattr(db, "_engine", broken)
    assert client.get("/health/ready").status_code == 503
    assert client.get("/health").status_code == 200  # liveness stays up



@pytest.mark.skipif(not os.environ["TEST_DATABASE_URL"].startswith("postgresql"),
                    reason="needs Postgres")
def test_app_role_grants(database):
    """app.grants as the schema owner: the app role can write data but never history."""
    from app import grants
    role = "govtest_app_role"
    with database.begin() as conn:
        if conn.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).scalar():
            conn.execute(text(f"DROP OWNED BY {role}"))
            conn.execute(text(f"DROP ROLE {role}"))
        conn.execute(text(f"CREATE ROLE {role} NOLOGIN"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS alembic_version (version_num varchar(32))"))
    assert grants.apply(database, role) and grants.apply(database, role)  # idempotent
    with database.connect() as conn:
        def can(table, priv):
            return conn.execute(text("SELECT has_table_privilege(:r, :t, :p)"),
                                {"r": role, "t": table, "p": priv}).scalar()
        assert can("responses", "UPDATE") and can("audit_events", "INSERT")
        assert not can("audit_events", "UPDATE") and not can("audit_events", "DELETE")
        assert not can("alembic_version", "UPDATE") and can("alembic_version", "SELECT")
    with database.begin() as conn:
        conn.execute(text(f"DROP OWNED BY {role}"))
        conn.execute(text(f"DROP ROLE {role}"))
