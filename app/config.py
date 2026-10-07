import os


def _set(name: str) -> set[str]:
    return {v.strip().lower() for v in os.environ.get(name, "").split(",") if v.strip()}


# Database: any SQLAlchemy URL. Production = Azure Database for PostgreSQL;
# local compose = the postgres container; tests = SQLite.
DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://governance:governance@localhost:5432/governance")
# password: the password is in DATABASE_URL (local, or a Key Vault-backed secret).
# entra   : passwordless - each new connection uses a Microsoft Entra token from the
#           managed identity as its password. DATABASE_URL then has the identity's
#           Postgres role name as the user and no password.
DATABASE_AUTH = os.environ.get("DATABASE_AUTH", "password")
# Migration job only: the app's (separate, least-privileged) Postgres role to grant to.
# See app/grants.py.
APP_DB_ROLE = os.environ.get("APP_DB_ROLE", "")

# Blob storage (defaults target Azurite; override in Azure)
BLOB_CONNECTION_STRING = os.environ.get(
    "BLOB_CONNECTION_STRING",
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    # Well-known Azurite key (not a secret)
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://localhost:10000/devstoreaccount1;",
)
# On Azure, set BLOB_ACCOUNT_URL (https://<account>.blob.core.windows.net) instead of a
# connection string: the app then authenticates with its managed identity (no keys).
BLOB_ACCOUNT_URL = os.environ.get("BLOB_ACCOUNT_URL", "")
BLOB_CONTAINER = os.environ.get("BLOB_CONTAINER", "artifacts")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "50"))

# Authentication.
#   easyauth - identity comes from Azure Container Apps / App Service built-in
#              auth (Entra ID) headers. ONLY safe behind Easy Auth, which
#              strips client-supplied X-MS-CLIENT-PRINCIPAL-* headers.
#   dev      - local development: pick a user from a switcher, no login.
AUTH_MODE = os.environ.get("AUTH_MODE", "easyauth")
# UPNs that are made admin on first sign-in (bootstrap; manage roles in-app after).
ADMIN_UPNS = _set("ADMIN_UPNS")
# Role given to anyone else on first sign-in.
DEFAULT_ROLE = os.environ.get("DEFAULT_ROLE", "viewer")

# Evidence repo: base URL of the audit-evidence repo, e.g.
# https://github.example.com/org/audit-evidence . Used to recognise repo links
# and warn when they are not pinned to a commit SHA.
EVIDENCE_REPO_BASE = os.environ.get("EVIDENCE_REPO_BASE", "").rstrip("/")
# Optional: pin branch links to commits via the GitHub API (read-only token with
# Contents: read on the evidence repo). GitHub Enterprise: https://<host>/api/v3
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_API_URL = os.environ.get("GITHUB_API_URL", "")

# Optional Teams notifications: a channel's incoming webhook URL (Teams Workflows
# "post to a channel when a webhook request is received"). APP_BASE_URL makes the
# notifications link back to the app, e.g. https://governance.internal.corp
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
APP_BASE_URL = os.environ.get("APP_BASE_URL", "")

# Azure scope sync (management groups + subscriptions) and AI catalog enrichment.
# Credentials resolve via DefaultAzureCredential (managed identity on Azure).
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
# Management group to sync from; defaults to the tenant root group (= tenant id).
AZURE_ROOT_MANAGEMENT_GROUP = os.environ.get("AZURE_ROOT_MANAGEMENT_GROUP", "") or AZURE_TENANT_ID
AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
GOOGLE_CLOUD_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")

# Per-CSP reference docs for AI enablement reviews (URLs, item mappings,
# standard positions). Point at a custom copy to change defaults without a rebuild.
REFERENCE_DOCS_PATH = os.environ.get(
    "REFERENCE_DOCS_PATH",
    os.path.join(os.path.dirname(__file__), "enrichment", "reference_docs.json"),
)
