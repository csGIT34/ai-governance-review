# Architecture

Governance Review records **proof that controls are performed**, in a form an auditor accepts:
who answered what, against which version of the control, with which evidence, who signed it
off, and every change in between.

It runs two kinds of assessment on one engine:

| Kind | Used for | Answers per | Sign-off |
|---|---|---|---|
| `controls` | Cloud infrastructure RCSA / audit evidence for Azure | control × scope node (tenant / management group / subscription), inherited downwards | per answer: preparer → reviewer, then close & attest the assessment |
| `checklist` | AI model / feature enablement reviews (see [ai-review-framework.md](ai-review-framework.md)) | checklist item (one implicit scope node) | one reviewer decision for the whole review, gated on blocker items |

The control document stays the **system of record** for controls. The app holds a reference
copy (a *library*) only so people have something to answer against.

## Stack

- **FastAPI** + **Jinja2** server-rendered pages. No SPA, no build step. One small vanilla JS
  file (`app/static/app.js`) does autosave, confirmations and lazy history panels.
- **SQLAlchemy 2** ORM + **Alembic** migrations. **PostgreSQL** in production (Azure Database
  for PostgreSQL Flexible Server) and in `docker compose`; **SQLite** for the test suite.
- **Azure Blob Storage** for files (Azurite locally).
- **Entra ID** sign-in through Azure Container Apps built-in authentication ("Easy Auth").
- Optional outbound calls: Azure Management Groups API (scope sync), Azure / Vertex AI model
  catalogs and CSP terms pages (AI reviews only), all via the app's managed identity.

## Code map

```
app/
  main.py            app wiring: middleware, routers, error pages, startup seeding
  config.py          every environment variable, with defaults for local dev
  db.py              engine/session setup (Postgres or SQLite)
  models.py          the whole schema - read this first
  audit.py           create / apply_changes / record: the ONLY way to change audited data
  auth.py            identity (Easy Auth headers or dev switcher), roles, CSRF guard
  web.py             render() and redirect() helpers, Jinja filters
  blob.py            write-once blob upload/download
  seed.py            built-in AI checklist library, dev users, --demo data
  checklist.py       seed data for the AI checklist (45 items)
  services/          business logic, no HTTP
    assessments.py   create (with snapshots), scope Tree, inheritance, rollup, close readiness
    responses.py     save with optimistic locking, lock rules, sign-off state machine
    evidence.py      evidence links (repo pin detection), immutable artifacts
    libraries.py     CSV / Excel-paste import
    scope.py         Azure management-group sync
    export.py        Excel workbook
    ai_review.py     catalog auto-fill, reference-doc snapshots, blocker gate
  routes/            thin HTTP layer: parse form -> call service -> commit -> redirect
    common.py        home, autosave, sign-off, evidence, artifacts, history, users
    controls.py      libraries, scope, control assessments, close, export, report
    issues.py        issues
    ai.py            AI reviews + reference-doc settings
  enrichment/        AI review: catalog clients, auto-fill rules, reference docs (+ JSON defaults)
  templates/         Jinja; _response.html is the shared answer editor
  static/            app.css, app.js
migrations/          Alembic (0002 makes audit_events append-only on Postgres)
tests/               pytest; conftest.py has the harness
```

## Data model

```
libraries 1─* library_items                 (reference copy of the control document)
    │ snapshot at creation
    ▼
assessments 1─* assessment_items            (frozen controls)
            1─* assessment_scope_nodes      (frozen subtree of scope_nodes, self-referencing)
            1─* responses  ── (assessment_item, assessment_scope_node) unique
                    1─* evidence_links ─? artifacts
                    1─* comments
            1─* issues     1─* evidence_links
            1─* artifacts
scope_nodes (tenant → management groups → subscriptions, self-referencing; never deleted)
users, settings
audit_events (append-only; entity_type + entity_id + assessment_id, {field: [before, after]})
```

Key rules:

- **Snapshots.** Creating an assessment copies the active library items into
  `assessment_items` and the active scope subtree into `assessment_scope_nodes`. Library edits,
  re-imports and scope syncs never change an existing assessment.
- **No row means no answer.** A `responses` row exists only where someone answered.
- **Inheritance** (`services/assessments.effective`): a node's effective answer is its own
  response, else the nearest ancestor's. A subscription inherits from its management group,
  which inherits from the tenant. "Override" creates the node's own response (a copy of the
  inherited text, or N/A). Draft overrides without evidence/comments/issues can be removed to
  inherit again; the deleted content stays in the audit log.
- **Rollup** counts each control's effective outcome over the *leaves* (subscriptions):
  `effective | partially_effective | ineffective | not_tested | na | in_progress | unanswered`.
  An answer is `in_progress` until it has a narrative and both ratings, plus test procedure and
  result unless operating effectiveness is "not tested". N/A needs a narrative saying why.
  A complete answer's outcome is the **worse** of design and operating effectiveness; if
  operation wasn't tested it is `not_tested` (never `effective`), unless the design is already
  partially effective or ineffective.

### Answer lifecycle (controls)

```
            save (any edit)                       review (reviewer ≠ preparer)
  draft ───────────────► draft ──prepare──► prepared ──────────────────────► reviewed (locked)
    ▲                       ▲                │  │                                │
    │                       └──── edit ──────┘  └──return (comment)──► returned  │
    └───────────────────────────── reopen (reviewer, comment) ◄──────────────────┘
```

- Editing a `prepared` answer (text or evidence) drops it back to `draft`.
- Evidence and comments attach to a node's *own* answer. On a node that inherits, the user
  must choose "answer differently here" first; an empty override is never created silently.
- `returned` answers stay returned until the preparer marks them prepared again.
- Closing the assessment requires every control × subscription to have a complete effective
  answer and every answer row to be `reviewed`. Closing records an attestation statement and
  locks everything; only an admin can reopen, with a reason.

### Concurrency

`responses.version` and `issues.version` are SQLAlchemy `version_id_col`s. The editor sends the
version it loaded; a mismatch returns **409** and nothing is written. The browser keeps the
user's text and tells them who saved first. The UPDATE itself is also guarded on the version, so
two simultaneous requests can't both win.

Sign-off actions (prepare / review / return / reopen) carry the version too, so a reviewer
can't sign off text that changed after they loaded the page. Every answer write and close/reopen
first takes a row lock on the assessment (`responses.lock`, `SELECT … FOR UPDATE` on Postgres),
so an answer can't slip in between the close checks and the close.

## Audit trail

`app/audit.py` is the only write path for audited data:

- `audit.create(db, actor, obj)` adds a row and logs its initial values.
- `audit.apply_changes(db, actor, obj, {field: value})` sets fields and logs
  `{field: [before, after]}` for the ones that actually changed (nothing if nothing changed).
- `audit.record(...)` logs an event without changing fields (exports, deletes).

Events are written in the same transaction as the change. Postgres migration `0002` installs
triggers that reject UPDATE, DELETE and TRUNCATE on `audit_events`. In production also revoke those rights
from the app's role (see README). Each assessment's log is on its **Activity** page and in the
Excel export.

Other evidence-integrity rules:

- Artifacts are never overwritten. Each upload gets a new blob path
  (`assessments/<id>/<uuid>/<name>`, `overwrite=False`) and a stored sha256.
- Evidence links are soft-deleted (`removed_at` / `removed_by`).
- Repo links are classified as pinned (URL contains a 40-hex commit SHA) or not pinned (a
  branch, which changes over time). Not-pinned links get a warning and are marked in exports.

## Security model

| Concern | Control |
|---|---|
| Authentication | Easy Auth (Entra ID) in front of the container; the app trusts `X-MS-CLIENT-PRINCIPAL-*` headers **only** because Easy Auth strips client-supplied ones. `AUTH_MODE=dev` is for local machines only. |
| Authorization | Roles in `users.role`: viewer < preparer < reviewer < admin, enforced per route with `require(role)`. New users get `DEFAULT_ROLE` (viewer); `ADMIN_UPNS` bootstraps admins. The last active admin can't be removed. |
| Segregation of duties | A reviewer can't sign off an answer they prepared, or whose content or evidence they changed since its last review (checked against the audit log). Only reviewers can close an issue or accept its risk. |
| CSRF | `auth.csrf_guard` rejects state-changing requests with `Sec-Fetch-Site: cross-site`, or a foreign `Origin` when Fetch Metadata is absent. |
| XSS | Jinja autoescaping everywhere. No user text is put into inline JS (`data-confirm` attributes instead). Uploaded HTML/SVG is always served as an attachment with `nosniff`. |
| SSRF | Only admins can trigger server-side fetches of arbitrary URLs (reference-doc settings). `reference_docs.check_public_url` blocks non-public addresses, including the cloud metadata endpoint. It is re-checked on every redirect hop, and every request the PDF renderer (Chromium) makes is routed through it. Residual risk: DNS rebinding between the check and the connection. |
| Uploads | Filenames sanitised; size capped by `MAX_UPLOAD_MB`. |
| Container | Runs as a non-root user. |

## Request flow (typical)

`routes/*` parse the form → load objects → call a `services/*` function (which uses `audit.*`
for every change) → `db.commit()` → `redirect(url, msg=/error=)` (PRG). The autosave endpoint is
the exception: it returns JSON for `app.js`. Service functions raise `responses.SaveError`
subclasses (`Forbidden` 403, `Conflict` 409, `Locked` 423) that routes turn into messages.
