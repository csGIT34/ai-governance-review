# Governance Review

Internal tool that records **proof that cloud controls are performed**, kept to audit standard.
Every answer, sign-off, piece of evidence and change is recorded with who did it and when.

- **Control assessments (RCSA)** for Azure. Answer each control from your control document
  for every subscription. Answer once at a management group and the subscriptions under it
  inherit, unless they override. Link evidence (commit-pinned links to the audit-evidence repo,
  uploaded files, or Azure Resource Graph query results), have a second person sign off each
  answer, raise issues for gaps, then close and attest. Export everything to Excel for the
  auditors. Next quarter starts from last quarter's answers; controls are assigned to people,
  who see what's theirs under **My work**; reviewers can sign off in bulk; Teams gets notified.
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
make test-azure-emulated   # managed identity + Blob over OAuth against the Floci AZ emulator
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
| `DATABASE_AUTH` | `password` | `entra`: passwordless, the managed identity's Entra token is the password |
| `RUN_MIGRATIONS` | `true` | Run migrations + seeding on container start; `false` on Azure (a job does it) |
| `BLOB_ACCOUNT_URL` | — | `https://<account>.blob.core.windows.net`: use the managed identity (no keys) |
| `BLOB_CONNECTION_STRING` | Azurite | Used when `BLOB_ACCOUNT_URL` is empty |
| `BLOB_CONTAINER` | `artifacts` | |
| `MAX_UPLOAD_MB` | `50` | |
| `AUTH_MODE` | `easyauth` | `easyauth` (behind Container Apps auth) or `dev` (local only, never deploy) |
| `ADMIN_UPNS` | — | Comma-separated UPNs made admin on first sign-in |
| `DEFAULT_ROLE` | `viewer` | Role for everyone else on first sign-in |
| `EVIDENCE_REPO_BASE` | — | e.g. `https://github.corp.com/org/audit-evidence`: links under it are checked for SHA pinning |
| `GITHUB_TOKEN`, `GITHUB_API_URL` | — | Pin branch links to commits via the API (Enterprise: `https://<host>/api/v3`) |
| `TEAMS_WEBHOOK_URL`, `APP_BASE_URL` | — | Teams channel notifications, with links back to the app |
| `AZURE_CLIENT_ID` | — | Client id of the user-assigned managed identity (Azure) |
| `AZURE_TENANT_ID` | — | Enables scope sync (root management group = tenant root group) |
| `AZURE_ROOT_MANAGEMENT_GROUP` | tenant id | Sync from a lower management group instead |
| `AZURE_SUBSCRIPTION_ID`, `GOOGLE_CLOUD_PROJECT` | — | AI review catalog enrichment (optional) |
| `REFERENCE_DOCS_PATH` | bundled JSON | AI review reference-doc defaults |

Azure calls use `DefaultAzureCredential`: the managed identity on Azure, or
`AZURE_CLIENT_ID`/`AZURE_CLIENT_SECRET`/`AZURE_TENANT_ID` locally (put them in `.env`; see
`.env.example`).

## Deploying to Azure

Target: **Azure Container Apps** (internal environment in a VNet) + **Azure Database for
PostgreSQL Flexible Server** + **Storage account**, everything private and authenticated with
managed identities. The full spec (resources, settings, roles, private DNS, egress, Easy
Auth, environment variables, database roles, jobs, rollout order and a verification
checklist) is in [docs/AZURE_DEPLOYMENT.md](docs/AZURE_DEPLOYMENT.md). It's meant to be mapped
onto your Terraform pattern modules.

## Moving this repo to work

- Push to your work GitHub, open it in Claude Code and run **`/onboard-work <governance repo>`**.
  It follows [docs/WORK_ONBOARDING.md](docs/WORK_ONBOARDING.md): read the governance repo, map
  the controls and evidence layout, map the Azure environment, then deploy using your
  Terraform patterns, stopping for your review at each step. `CLAUDE.md` has the project map,
  commands and the rules that must not be broken.
- Commit history carries the author email of whoever made each commit here. Squash or rewrite
  it first if that matters at work.
- CI (`.github/workflows/ci.yml`) runs the tests on SQLite and Postgres plus the browser test.
  GitHub Enterprise runners need Docker for the Postgres service.
