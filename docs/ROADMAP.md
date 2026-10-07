# Roadmap

What's built is the MVP: control libraries (reference copy of the control document), Azure
scope sync, assessments with management-group inheritance, autosaving answers, per-answer
sign-off with segregation of duties, close & attest, issues, Excel export, printable report,
and the AI enablement review on the same engine. Everything is audited.

Items below are ordered by value for a periodic cloud RCSA. Each has acceptance criteria so it
can be picked up and verified on its own. Follow the rules in CLAUDE.md (every change via
`app/audit.py`, snapshots stay frozen, tests on SQLite + Postgres).

## P1 — makes the next quarter fast

### 1. Carry forward from a previous assessment
Most narratives barely change between quarters, so the next RCSA should start from the last one.
- New-assessment form gets an optional "Start from" (a closed assessment using a library with
  overlapping refs).
- For each item whose `ref` exists in both, and each scope node with the same `scope_node_id`,
  copy narrative, test procedure and evidence links into a new **draft** response. Ratings and
  test result start empty: they have to be re-tested.
- The copy is audited (`note="carried forward from assessment <id>"`) and each copied response
  says where it came from in the UI.
- **Done when:** a test creates A, answers and closes it, creates B from A, and B has draft
  answers with A's narratives, empty ratings, and nothing changed in A.

### 2. Assignment and a "my work" queue
- Add an optional preparer (user) per assessment item, defaulting to a library-level default
  owner. Show it on the overview, and allow bulk assign by category.
- A `/my` page lists answers I need to write (assigned, incomplete), answers returned to me,
  and, for reviewers, answers waiting for my review (prepared by someone else).
- **Done when:** tests cover each list, including that reviewers never see their own prepared
  answers in "to review".

### 3. Pin evidence links automatically
- With `GITHUB_TOKEN` (GitHub Enterprise: `GITHUB_API_URL`) set, adding a branch link to the
  evidence repo resolves the branch to the current commit SHA (`GET /repos/{o}/{r}/commits/{ref}`)
  and stores the permalink instead, keeping the original in the title. It also checks that the
  file exists at that commit (`GET /repos/{o}/{r}/contents/{path}?ref={sha}`).
- No token: keep today's behaviour (warn, mark "not pinned").
- **Done when:** tests stub the GitHub API and cover pinned, branch → pinned, missing file, and
  API down (link still added, flagged).

### 4. Bulk sign-off
- On the overview, reviewers can filter "Waiting for review" and sign off several answers at
  once, with one optional comment. Segregation of duties still applies per answer (skip and
  report any they prepared).
- **Done when:** a test signs off N answers in one request, skips the reviewer's own, and the
  audit log has one event per answer.

## P2 — less typing, more automatic evidence

### 5. Azure auto-evidence snapshots
- An "Attach Azure evidence" action on a control runs a configured Azure Resource Graph query
  (stored on the library item, e.g. policy compliance for an assignment, or Defender plan
  status) scoped to the node's subscriptions. It stores the JSON result as an artifact (sha256)
  and links it as evidence.
- **Done when:** the query and result are stored, re-running creates a new artifact (never
  overwrites), and tests stub Resource Graph.

### 6. Notifications
- Teams incoming webhook (`TEAMS_WEBHOOK_URL`): answer returned → preparer; prepared →
  reviewers; issue due in 7 days or overdue → owner. A daily job (Container Apps job or cron)
  handles due dates.
- **Done when:** notification payloads are unit-tested and sending failures never block saves.

### 7. Matrix view
- Controls (rows) × management groups / subscriptions (columns), with the effective outcome
  per cell and inherited cells styled differently. Click a cell to open the editor at that node.
  Must stay usable at 100 subscriptions: collapse to management groups by default.

### 8. Infrastructure as code
- Bicep for: Container App (Easy Auth with Entra ID, ingress, managed identity), Azure Database
  for PostgreSQL Flexible Server (private access), Storage account (blob versioning + container
  immutability policy), Key Vault for the DB password, role assignments (Management Group
  Reader on the root management group, Storage Blob Data Contributor).
- Migrations as a Container Apps job instead of on container start (needed once there's more
  than one replica).

### 9. Passwordless Postgres
- Use the managed identity's Entra token as the Postgres password (SQLAlchemy `do_connect`
  event fetching a token for `https://ossrdbms-aad.database.windows.net/.default`), removing the
  stored password.

### 10. AI review decisions that go stale
- After a decision, answers can still change (by hand or by auto-fill). If a blocker item
  stops being Pass/N-A after an "approved" decision, flag the review as "changed since decision"
  on the list and review page, and require a new decision.

## P3 — nice to have

- **DNS rebinding:** pin the resolved IP for server-side fetches (custom httpx transport) so a
  hostname can't resolve to a public address at check time and a private one at connect time.

- **Hybrid controls:** a subscription answer that *adds to* the inherited one instead of
  replacing it (show both in the editor and the export).
- **Server-side PDF** of the report with Playwright (already in the image), stored as an
  artifact when an assessment is closed, so the attested state has a frozen PDF.
- **Markdown** rendering for narratives (sanitised, e.g. `markdown-it-py` + `nh3`).
- **Issue dashboard:** overdue issues across assessments, ageing, and a CSV export.
- **Data retention:** document backup and point-in-time restore for Postgres, and a blob
  lifecycle policy that never deletes evidence inside the retention period.
