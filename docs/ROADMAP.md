# Roadmap

**Built:** control libraries (a reference copy of the control document, imported from
Excel/CSV), Azure scope sync, assessments with management-group inheritance, autosaving
answers, per-answer sign-off with segregation of duties, close & attest, issues, Excel export,
printable report, carry-forward between assessments, control assignment and a "My work" queue,
bulk sign-off, commit-pinned evidence links (GitHub API), Azure Resource Graph evidence, Teams
notifications, managed-identity auth to Blob and Postgres, and the AI enablement review on the
same engine. Everything is audited.

## Next, at work

These need the real environment; see [WORK_ONBOARDING.md](WORK_ONBOARDING.md).

- **Infrastructure:** Terraform for [AZURE_DEPLOYMENT.md](AZURE_DEPLOYMENT.md) using the
  organisation's pattern modules. Ask before writing it.
- **Control import:** a converter from the control document's format to the library import
  columns, if the document is structured (markdown tables, YAML…). Keep it in `tools/` with a test.
- **Suggest evidence from the governance repo:** if evidence files are organised per control
  (control id in the path), list candidate files for a control via the GitHub API at the
  period's commit, so preparers pick instead of pasting links.
  - **Done when:** a test with a stubbed tree listing shows the right candidates for a ref,
    and picking one adds a pinned link.
- **Evidence queries:** map existing KQL from the governance repo onto controls' `evidence_query`.

## Open items (can be built anywhere)

Each item has acceptance criteria. Follow the rules in CLAUDE.md: every change goes through
`app/audit.py`, snapshots stay frozen, and tests must pass on SQLite and Postgres.

### Matrix view
- Controls (rows) × management groups / subscriptions (columns), with the effective outcome
  per cell and inherited cells styled differently. Click a cell to open the editor at that node.
  Must stay usable at 100 subscriptions: collapse to management groups by default.

### AI review decisions that go stale
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
