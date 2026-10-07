# AI model / feature enablement review — framework

The **checklist** assessment kind: reviews of new AI models and features on **Azure** and
**GCP** before enabling them on the company's LiteLLM-based AI platform. (System design is in
[ARCHITECTURE.md](ARCHITECTURE.md); this document is the review framework itself.)

## Why this exists

Developers request new models/features (e.g., "enable GPT-4.1 in Azure OpenAI", "enable Gemini 2.5
Flash on Vertex AI", "turn on batch API"). Before the platform team enables anything, a structured
review must answer: *is this safe, compliant, supportable, and affordable?* Today that knowledge is
tribal. This app makes the review repeatable, auditable, and historical.

## Core concepts

| Concept | Description |
|---|---|
| **Review** | One request to enable a model/feature on one CSP: an assessment of kind `checklist`. Carries the answers, an enrichment snapshot, artifacts, and a decision. |
| **Checklist library** | The review framework (see below), seeded from `app/checklist.py` into the `ai-enablement` library on first start and editable under Libraries. Each review snapshots the items at creation, so later edits never rewrite historical records. |
| **Enrichment snapshot** | Point-in-time metadata pulled from the CSP catalog APIs (lifecycle status, deprecation dates, capabilities) stored on the review as evidence of what the CSP said *at review time*. |
| **Artifacts** | Files (screenshots, exported docs, pricing sheets) uploaded as evidence, stored in Blob Storage. |
| **Decision** | Approved / Approved with conditions / Rejected, recorded by a signed-in reviewer with conditions/rationale and a re-review date. Superseded decisions stay in the history. |

## The review framework (what we check before enabling anything)

Fourteen categories, ~45 items. Each item has a severity:

- **blocker** — must be `Pass` or `N/A` before an unconditional Approve.
- **required** — must be addressed; gaps should become conditions on the approval.
- **recommended** — best practice; reviewer judgment.

### A. Request Context
- Business justification and intended use case documented.
- Exact model/feature identity (name, version, modality, endpoint type — chat, embeddings, batch, realtime, fine-tuning, agents/tools).
- Target regions identified and justified.
- Data classification of the workloads that will call this model (public / internal / confidential / regulated).

### B. Lifecycle & Support Status
- GA vs Preview/Beta (Azure: model lifecycle status; GCP: launch stage). Previews usually lack SLA and compliance coverage.
- Deprecation/retirement dates known (Azure model retirement dates; GCP model version discontinuation) and a re-review date set.
- SLA coverage confirmed for the specific feature.
- Version pinning vs auto-upgrade policy (Azure deployment upgrade options; GCP auto-updated aliases).

### C. Data Privacy & Residency
- Processing geography matches policy. **Azure**: Global vs DataZone vs Regional deployment types route inference cross-region differently. **GCP**: global vs regional endpoints; ML-processing location commitments.
- Data-at-rest location for fine-tunes, uploaded files, batch jobs.
- Confirmation customer data is **not used to train** models — verified for this *specific* service/feature, not assumed.
- Prompt/output retention: Azure abuse monitoring stores prompts/completions up to 30 days unless approved for modified abuse monitoring; GCP caching of prompts (can be disabled) and zero-retention configuration.
- Prompt caching behavior and implications.
- Third-party/partner models (Anthropic, Meta, Mistral via Foundry / Model Garden): data handling may fall under *different* terms than first-party models — review the partner terms.

### D. Compliance & Certifications
- Service + feature in scope for required certifications (SOC 2, ISO 27001/27018) — check the CSP's service-level compliance scope, not the platform-level marketing page.
- HIPAA/BAA coverage if PHI could flow through.
- Other regulatory needs (FedRAMP, PCI, EU AI Act obligations) as applicable.
- Preview-feature compliance gaps explicitly acknowledged.

### E. Legal, Licensing & IP
- Model license/terms reviewed (open-weight licenses like Llama/Gemma have use restrictions; partner models have their own terms).
- CSP acceptable-use policy vs the intended use case.
- IP indemnification conditions: Azure Customer Copyright Commitment and Google's generative AI indemnity both have *conditions* (e.g., required content filters/safety settings stay on).
- Preview/beta supplemental terms reviewed.

### F. Security & Network
- Private connectivity (Azure Private Endpoint / GCP Private Service Connect) supported and planned; public network access per policy.
- Encryption in transit; customer-managed keys (CMK/CMEK) supported if required.
- AuthN: Entra ID / GCP IAM preferred; API keys disabled, or rotated and vaulted.
- Data exfiltration controls (GCP VPC Service Controls perimeter support; Azure network/egress restrictions).
- New attack surface reviewed (tool use / agents / grounding-with-search send data to additional services).

### G. Identity & Access
- RBAC: who can deploy/manage vs who can invoke.
- Least-privilege identity for the LiteLLM proxy (managed identity / workload identity over static keys).
- Key rotation and secret storage (Key Vault / Secret Manager).

### H. Responsible AI & Content Safety
- Content filtering configured per policy (Azure AI Content Safety filter config; Vertex safety filter thresholds).
- Abuse-monitoring posture decided (privacy vs detection trade-off).
- Prompt-injection / jailbreak protections (Azure Prompt Shields; GCP Model Armor) for exposed use cases.
- Internal Responsible-AI assessment for **new modalities** (image/video/voice generation → impersonation & deepfake risk).
- Grounding/citation behavior and hallucination risk vs the use case.

### I. Observability & Audit
- Diagnostic/audit logs enabled and shipped to SIEM (Azure Diagnostic Settings; GCP Cloud Audit Logs).
- Per-model usage metrics (tokens, latency, errors) on existing dashboards.
- Request/response logging policy decided (PII implications) and reflected in LiteLLM logging config.
- Alerting on throttling/quota exhaustion.

### J. Quota & Capacity
- Default quotas (TPM/RPM) per region documented and sufficient for forecast load.
- Quota-increase path and lead time; provisioned throughput (Azure PTU / GCP Provisioned Throughput) evaluated if needed.
- Enough regional deployments to satisfy the LiteLLM load-balancing/HA strategy.
- 429/throttling behavior tested against LiteLLM retry/fallback/cooldown config.

### K. Cost & Commercial
- Pricing model understood and unit costs recorded (input/output/cached tokens, images, reasoning tokens, PTU hourly).
- Cost comparison vs already-enabled models with the same capability.
- Budgets, alerts, and chargeback tagging in place.
- Hidden cost drivers reviewed (long context, reasoning tokens, multimodal inputs).

### L. Platform Integration (LiteLLM)
- LiteLLM supports the provider/model and required features pass through (streaming, function calling, vision, structured output, API version).
- Model alias and routing strategy defined (weights, fallbacks, cooldown).
- Cross-CSP feature parity documented if the same model class is served from both clouds.
- Latency/load benchmark vs requirements.

### M. Rollout & Rollback
- Enablement plan (environments, teams, gradual rollout).
- Rollback plan (disable in LiteLLM config, remove deployments).
- Developer communication (docs, model card, known limitations).
- Re-review owner and date assigned.

## Catalog auto-fill

Catalog facts that directly answer checklist items are auto-filled with `[auto]`-prefixed notes
and the snapshot artifact as evidence: **A2** (exact identity confirmed / version ambiguity
flagged), **B1** (GA → pass; preview → needs-info, since enabling previews is a policy
decision), and **B2** (Azure retirement dates; the Vertex catalog doesn't expose them).
Auto-fill only touches items still *unreviewed* — reviewer input is never overwritten — and
each snapshot records which items it updated (`checklist_updates`). Everything else stays a
human judgment. Reference-doc snapshots work the same way for curated standard positions
(C3, C4, E3), withheld if the source page changed since validation.

## Out of scope (deliberate)

- Notifications/integrations (Teams/Slack/ServiceNow).
- Multi-step approval chains — one reviewer decision per review.
- AWS (owned by another team) — `csp` is an enum, adding `aws` later is a template + enum change.
