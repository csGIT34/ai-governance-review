# Onboarding at work (playbook for Claude Code)

This repo was built in a home lab with no access to the real control document, the governance
(audit-evidence) repo or the Azure environment. This playbook adapts it to those, in order.
Start it with `/onboard-work`, or paste the prompt at the bottom.

**Rules while doing this** (on top of CLAUDE.md):
- Ask the owner before writing any Terraform. The organisation has Terraform pattern modules;
  map `docs/AZURE_DEPLOYMENT.md` onto them rather than writing raw resources.
- Don't add GitHub Actions workflows unless asked (`.github/workflows/ci.yml` already exists).
- Don't spin up paid Azure resources without asking.
- Never commit secrets, tenant ids or internal hostnames into tracked files except where the
  owner says so. Use `.env` (git-ignored) or deployment config.

## Step 1 — Get oriented (no changes)

1. Read `CLAUDE.md`, `docs/ARCHITECTURE.md`, `docs/AZURE_DEPLOYMENT.md`, `docs/ROADMAP.md`.
2. Run `make test` (or `pip install -r requirements-dev.txt && python -m pytest -q`) to confirm
   a green baseline on this machine. Report anything that fails to run here (proxies, package
   mirrors, Docker availability).

## Step 2 — Understand the governance repo

Ask the owner for its local path or URL, then inventory it (read-only):

- **Where are the controls defined?** (markdown tables, one md file per control, YAML, an
  exported spreadsheet…) Record: control id format, title, description, owner, frequency,
  framework mappings, testing/evidence guidance, and any KQL / Azure Policy / script used to
  test the control.
- **How is evidence produced and stored?** Folder layout (per control? per quarter? per
  subscription?), file naming, and what the evidence-producing code does and how it runs.
- **Which branch is authoritative**, and is there a release/tag per audit period?

Write the findings in a short note for the owner, then propose, and wait for a yes before
doing, these mappings:

| App concept | Proposal to make |
|---|---|
| Library item columns (`ref, title, description, category, owner, frequency, framework_refs, guidance, evidence_query`) | How each column is filled from the control document. If the document is structured, write a small converter (keep it in `tools/`, with a test) that outputs the TSV/CSV the **Libraries → Import** box accepts. |
| `evidence_query` | Reuse existing KQL from the governance repo where a control is tested with Azure Resource Graph. |
| Evidence links | `EVIDENCE_REPO_BASE` value. If evidence files are organised per control, propose roadmap item "suggest evidence files for a control from the repo layout" (match by control id in the path) and build it only if the owner agrees. |
| Pinning | A read-only token for the GitHub Enterprise API (`GITHUB_TOKEN`, `GITHUB_API_URL`): GitHub App installation token or fine-grained PAT with Contents: read on that repo. |

## Step 3 — Map the Azure environment

Ask the owner (don't guess):

- Tenant id; the management group to assess (tenant root group or lower); subscriptions out of scope.
- Landing zone / network: hub-spoke? central firewall egress allow-list process? private DNS
  ownership? How do internal apps get a DNS name?
- Existing shared services to reuse: ACR, Log Analytics, Key Vault, Postgres server.
- Terraform pattern modules available for each resource in `docs/AZURE_DEPLOYMENT.md`, and
  where infra code lives (this repo, or a separate infra repo).
- Evidence retention period (sets the blob immutability policy) and backup/DR requirements.
- Who the first admins and reviewers are; whether anyone should get a default role other than viewer.
- Is the AI model enablement review needed at work? (It can simply stay unused.)

Then produce a filled-in copy of the spec's tables for this environment (names, SKUs, CIDRs,
DNS) for the owner to review, and only then the Terraform, using their patterns.

## Step 4 — Configure, deploy, verify

1. Entra app registration + enterprise app assignment (spec §7).
2. Terraform apply (owner runs or approves).
3. Postgres roles SQL step 1 → image build/push → migration job → SQL step 2 → app revision (spec §10).
4. Walk the verification checklist (spec §11) with the owner and report each item.
5. Import the controls, sync the scope, create a pilot assessment for one management group,
   and have a preparer and a reviewer run one control end to end (answer, evidence, prepare,
   sign off, export).

## Step 5 — Hand back

Summarise what was configured and where (no secrets), what was verified, what's left, and
propose the next roadmap items given what you learned about the governance repo.

---

### Kickoff prompt (if not using `/onboard-work`)

> Follow docs/WORK_ONBOARDING.md step by step. Our governance repo is at `<path or URL>`.
> Start with steps 1 and 2 and stop for my review before changing anything.
