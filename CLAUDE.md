# CLAUDE.md

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

---

# Project: Governance Review

Audit-evidence app: teams record **proof that cloud controls are performed** (Azure RCSA) and
review AI model enablement requests. Auditors rely on the output, so **integrity beats features**.
Read `docs/ARCHITECTURE.md` before non-trivial changes; open work is in `docs/ROADMAP.md`.
Setting this up at work for the first time? Follow `docs/WORK_ONBOARDING.md` (`/onboard-work`).
Deployment target and settings: `docs/AZURE_DEPLOYMENT.md`. **Ask the owner before writing any
Terraform** (they have pattern modules), before adding CI workflows, and before creating paid
Azure resources.

## Stack & commands

FastAPI + Jinja2 (server-rendered, no build step) · SQLAlchemy 2 + Alembic · Postgres (SQLite in
tests) · Azure Blob · Entra ID via Container Apps Easy Auth · vanilla JS in `app/static/app.js`.

```sh
make test        # pytest on SQLite - run before every commit
make test-pg     # same suite on Postgres (Docker) - run when touching models/queries/migrations
make e2e         # Playwright browser test of autosave - run when touching app.js / _response.html
make test-azure-emulated  # managed identity + Blob OAuth against the Floci AZ emulator (no Azure cost)
make up          # docker compose: app on :8000 with AUTH_MODE=dev user switcher
make revision m="..."   # after editing app/models.py; review the generated file
```

## Rules that must not be broken

1. **Every change to audited data goes through `app/audit.py`** (`create`, `apply_changes`,
   `record`) in the same transaction. Never `setattr` + commit on a model without it.
2. **`audit_events` is append-only.** Never UPDATE/DELETE it (a Postgres trigger rejects it anyway).
3. **Assessments are frozen snapshots.** Never make `assessment_items` / `assessment_scope_nodes`
   follow later library or scope edits, and never rewrite them after creation. The one
   exception is `assessment_items.assignee` (who answers), changed only via audited assignment.
4. **Evidence is immutable.** Artifacts are never overwritten (new blob path each upload);
   evidence links are soft-deleted (`removed_at`). Scope nodes are deactivated, not deleted.
5. **Locks and sign-off live in `services/responses.py`** (`lock_reason`, `transition`). New write
   paths to responses must go through `save()` / `for_edit()` so locks, optimistic versioning
   and prepared→draft demotion apply. Sign-off actions must carry the version the user saw.
   Keep segregation of duties: a reviewer never signs off an answer they prepared or edited.
6. **Every route declares a role**: `Depends(current_user)` to read, `require("preparer" |
   "reviewer" | "admin")` to write. Viewers are read-only.
7. **No user text in inline JS** (use `data-*` attributes); serve uploads as attachments unless
   PDF/image/plain text; server-side fetches of user-supplied URLs go through
   `reference_docs.check_public_url`.
8. Schema changes need an Alembic migration that `alembic check` agrees with. Postgres-only SQL
   goes in its own migration, guarded on `op.get_bind().dialect.name`.

## Conventions

- Routes stay thin: parse form → call `app/services/*` → `db.commit()` → `redirect(url, msg=..., error=...)`
  (post/redirect/get). Services raise `responses.SaveError` subclasses; routes turn them into messages.
- Templates: `render(request, "x.html", ctx)` from `app/web.py`. `_response.html` holds the
  shared answer editor macro used by both assessment kinds; `can(user, role)` is a Jinja global.
- Tests: `tests/conftest.py` gives `client` (dev auth, starts as admin), `session`, `as_user()`,
  in-memory blobs, and a fresh schema per test. Add a test for every behaviour change, and
  make it pass on both SQLite and Postgres.
- Keep external calls stubbable: tests monkeypatch module functions such as
  `scope.fetch_hierarchy`, `scope._token`, `github._get`, `notify._post`, `azure_catalog.enrich`
  and `reference_docs.fetch`. Notifications go through `BackgroundTasks` after commit and must
  never raise into a request.
- Azure auth: never add account keys or DB passwords for Azure. Blob uses `BLOB_ACCOUNT_URL`
  + `DefaultAzureCredential`; Postgres uses `DATABASE_AUTH=entra` (token as password, `app/db.py`).

## Gotchas

- SQLite returns naive datetimes: compare through `models.aware()`.
- `Response`/`Issue` use SQLAlchemy `version_id_col`. Don't set `version` by hand; `db.flush()` bumps it.
- Form uploads are `starlette.datastructures.UploadFile`, not FastAPI's subclass (matters for `isinstance`).
- `/assessments/{aid:int}` must keep its `:int`, or it swallows `/assessments/new/...`.
- Don't put `<div>` inside `<p>` in templates: the parser closes the `<p>` and the layout breaks.
- Alembic `Config.set_main_option` needs `%` escaped as `%%`.
- New NOT NULL columns need `server_default` in the migration (existing rows), and named
  constraints (use `op.batch_alter_table` so SQLite works too).
- Azure SDK clients refuse bearer tokens over plain HTTP; emulators need TLS and their whole
  CA chain in `REQUESTS_CA_BUNDLE` (see `tests/test_azure_emulated.py`).
