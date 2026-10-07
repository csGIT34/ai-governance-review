# Governance Review

Internal tool that records **proof that cloud controls are performed**, kept to audit standard.
Every answer, sign-off, piece of evidence and change is recorded with who did it and when.

- **Control assessments (RCSA)** for Azure. Answer each control from your control document
  for every subscription. Answer once at a management group and the subscriptions under it
  inherit, unless they override. Link evidence (SHA-pinned links to the audit-evidence repo, or
  uploaded files), have a second person sign off each answer, raise issues for gaps, then close
  and attest. Export everything to Excel for the auditors.
- **AI model / feature enablement reviews** on Azure and GCP: a 45-item checklist with catalog
  auto-fill, CSP terms snapshots and a reviewer decision. See
  [docs/ai-review-framework.md](docs/ai-review-framework.md).

How it works: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). What's next:
[docs/ROADMAP.md](docs/ROADMAP.md).

## Run it locally

Prerequisite: Docker.

```sh
docker compose up --build
docker compose exec app python -m app.seed --demo   # optional: example controls + scope tree
```

Open **http://localhost:8000**. Local runs use `AUTH_MODE=dev`: switch between Alice (admin),
Bob (reviewer), Carol (preparer) and Victor (viewer) from the top-right menu. That lets you try
the preparer → reviewer flow on your own. Data persists in the `pg-data` and `azurite-data`
volumes; `docker compose down -v` wipes it.

### Without Docker (tests and quick iteration)

```sh
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
make test                  # SQLite, no services needed
make test-pg               # same suite on Postgres (starts a throwaway container)
make e2e                   # browser test of autosave (needs: playwright install chromium)
```

## Using it (control assessment)

1. **Libraries → New controls library.** Link the control document and its version, then
   paste the control table from Excel (header row: `ref, title, description, category, owner,
   frequency, framework_refs, guidance`; names like "Control ID" also work). Re-importing
   updates by `ref`.
2. **Scope → Sync from Azure**, or add the tenant, management groups and subscriptions by hand.
3. **+ New → Control assessment.** Pick the library, the scope root and the period. Controls
   and scope are frozen from that moment.
4. Open a control and pick a level in the scope tree. Type how the control is performed, then
   fill in the ratings, the test you did and its result, and any exceptions. **Answers save as
   you type.** Answer at a management group when the control is implemented centrally.
5. Add evidence: a permalink to the evidence repo file (press `y` on the GitHub file page to get
   one pinned to a commit), or upload a file. Raise an issue for each real gap.
6. **Mark prepared.** A reviewer (not the preparer) signs it off or returns it with a comment.
7. **Close & attest** once every control has a complete, signed-off answer for every
   subscription. Export to Excel or print the report.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres | SQLAlchemy URL, e.g. `postgresql+psycopg://user:pw@host:5432/db?sslmode=require` |
| `BLOB_CONNECTION_STRING` | Azurite | Storage account for files |
| `BLOB_CONTAINER` | `artifacts` | |
| `MAX_UPLOAD_MB` | `50` | |
| `AUTH_MODE` | `easyauth` | `easyauth` (behind Container Apps auth) or `dev` (local only, never deploy) |
| `ADMIN_UPNS` | — | Comma-separated UPNs made admin on first sign-in |
| `DEFAULT_ROLE` | `viewer` | Role for everyone else on first sign-in |
| `EVIDENCE_REPO_BASE` | — | e.g. `https://github.corp.com/org/audit-evidence`: links under it are checked for SHA pinning |
| `AZURE_TENANT_ID` | — | Enables scope sync (root management group = tenant root group) |
| `AZURE_ROOT_MANAGEMENT_GROUP` | tenant id | Sync from a lower management group instead |
| `AZURE_SUBSCRIPTION_ID`, `GOOGLE_CLOUD_PROJECT` | — | AI review catalog enrichment (optional) |
| `REFERENCE_DOCS_PATH` | bundled JSON | AI review reference-doc defaults |

Azure calls use `DefaultAzureCredential`: the managed identity on Azure, or
`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` locally (put them in `.env`; see
`.env.example`).

## Deploying to Azure

One container. Services:

| Local (compose) | Azure |
|---|---|
| `app` | **Azure Container Apps** with built-in authentication (Microsoft / Entra ID provider, "require authentication") and a system-assigned managed identity |
| `db` (Postgres 16) | **Azure Database for PostgreSQL Flexible Server**, private access, `sslmode=require` |
| `azurite` | **Storage account** with blob versioning, soft delete and a time-based immutability policy on the `artifacts` container |

Checklist:

1. Container App env: `DATABASE_URL` (secret / Key Vault reference), `BLOB_CONNECTION_STRING`,
   `AUTH_MODE=easyauth`, `ADMIN_UPNS=<you>`, `AZURE_TENANT_ID`, `EVIDENCE_REPO_BASE`.
2. Easy Auth **must** be enabled with unauthenticated requests rejected. The app trusts its
   identity headers.
3. Managed identity: **Management Group Reader** on the root (or chosen) management group for
   scope sync. Add **Reader** on a subscription for AI catalog enrichment if used.
4. The container runs `alembic upgrade head` on start. With more than one replica, run it as a
   separate job before rolling out instead.
5. Make the audit log append-only for the app's database role too (the migration's trigger
   already blocks UPDATE/DELETE):
   ```sql
   REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM <app_role>;
   ```
6. Postgres backups / PITR retention and blob immutability should match your evidence
   retention policy.

## Moving this repo to work

- Push to your work GitHub, then point your work Claude Code at `CLAUDE.md`. It has the
  project map, commands and the rules that must not be broken.
- Commit history carries the author email of whoever made each commit here. Squash or rewrite
  it first if that matters at work.
- CI (`.github/workflows/ci.yml`) runs the tests on SQLite and Postgres plus the browser test.
  GitHub Enterprise runners need Docker for the Postgres service.
