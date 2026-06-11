"""The review framework: versioned checklist template.

A copy of ITEMS is embedded into every review at creation time so that
editing this template never rewrites historical review records.

Severity:
  blocker     - must be Pass or N/A before an unconditional Approve
  required    - gaps should become conditions on the approval
  recommended - reviewer judgment
"""

CHECKLIST_VERSION = "1.0"

STATUSES = ["unreviewed", "pass", "fail", "na", "needs_info"]

ITEMS = [
    # A. Request Context
    {"id": "A1", "category": "A. Request Context", "severity": "required",
     "title": "Business justification documented",
     "guidance": "Why is this model/feature needed? Which app/team, what capability gap does it fill vs already-enabled models?"},
    {"id": "A2", "category": "A. Request Context", "severity": "blocker",
     "title": "Exact model/feature identity confirmed",
     "guidance": "Precise name, version, modality, and endpoint type (chat, embeddings, batch, realtime, fine-tuning, agents/tools). 'Gemini' is not a reviewable unit; 'gemini-2.5-flash on the global endpoint, chat completions' is."},
    {"id": "A3", "category": "A. Request Context", "severity": "required",
     "title": "Target regions identified and justified",
     "guidance": "Which regions will host deployments and why (capacity, latency, residency)."},
    {"id": "A4", "category": "A. Request Context", "severity": "blocker",
     "title": "Data classification of calling workloads",
     "guidance": "Highest sensitivity of data that will flow through this model (public / internal / confidential / regulated). Drives the depth required in C, D, F."},

    # B. Lifecycle & Support Status
    {"id": "B1", "category": "B. Lifecycle & Support", "severity": "blocker",
     "title": "GA vs Preview/Beta status confirmed",
     "guidance": "Previews typically lack SLA and compliance coverage.",
     "azure": "Check model lifecycle status in the Foundry model catalog / ARM models API (also shown by Enrich).",
     "gcp": "Check the model's launch stage (GA / Preview / Experimental) in Vertex docs or via Enrich."},
    {"id": "B2", "category": "B. Lifecycle & Support", "severity": "required",
     "title": "Deprecation / retirement dates known",
     "guidance": "Record the retirement date and set the review's re-review date before it.",
     "azure": "Azure publishes model retirement dates; the Enrich snapshot includes deprecation dates per version.",
     "gcp": "GCP publishes model version discontinuation dates."},
    {"id": "B3", "category": "B. Lifecycle & Support", "severity": "required",
     "title": "SLA coverage confirmed",
     "guidance": "Does the service SLA cover this specific feature/deployment type? Preview features are usually excluded."},
    {"id": "B4", "category": "B. Lifecycle & Support", "severity": "required",
     "title": "Version pinning / auto-upgrade policy decided",
     "guidance": "Pin a specific version or accept auto-upgrades?",
     "azure": "Deployments have upgrade options (auto-upgrade to default vs pinned).",
     "gcp": "Avoid auto-updated aliases in production unless explicitly accepted; pin dated versions."},

    # C. Data Privacy & Residency
    {"id": "C1", "category": "C. Data Privacy & Residency", "severity": "blocker",
     "title": "Processing geography matches residency policy",
     "guidance": "Where are prompts/outputs processed at inference time?",
     "azure": "Deployment type matters: Global / DataZone / Regional route inference to different geographies.",
     "gcp": "Global vs regional endpoints; check ML-processing location commitments for the model."},
    {"id": "C2", "category": "C. Data Privacy & Residency", "severity": "required",
     "title": "Data-at-rest location for stored assets",
     "guidance": "Fine-tuning data, uploaded files, batch job inputs/outputs - where do they live?"},
    {"id": "C3", "category": "C. Data Privacy & Residency", "severity": "blocker",
     "title": "Customer data not used for training - verified for THIS feature",
     "guidance": "Verify in the service-specific terms, not the platform marketing page. Watch for preview features with different terms."},
    {"id": "C4", "category": "C. Data Privacy & Residency", "severity": "blocker",
     "title": "Prompt/output retention understood and acceptable",
     "guidance": "How long does the CSP retain prompts and completions, and for what purpose?",
     "azure": "Abuse monitoring stores prompts/completions up to 30 days unless the subscription is approved for modified abuse monitoring.",
     "gcp": "Prompts may be cached (default ~24h, can be disabled); zero-data-retention configuration available for some services."},
    {"id": "C5", "category": "C. Data Privacy & Residency", "severity": "recommended",
     "title": "Prompt caching behavior reviewed",
     "guidance": "Does the feature cache prompts for performance/cost? Where, and is it acceptable for the data classification?"},
    {"id": "C6", "category": "C. Data Privacy & Residency", "severity": "blocker",
     "title": "Third-party / partner model terms reviewed (if applicable)",
     "guidance": "Models from partners (Anthropic, Meta, Mistral, etc.) hosted on the CSP may be covered by different data-handling terms than first-party models. N/A for first-party models.",
     "azure": "Partner & community models in Foundry; check 'sold by' and the partner's terms.",
     "gcp": "Model Garden partner models (e.g., Claude on Vertex); review the partner addendum."},

    # D. Compliance & Certifications
    {"id": "D1", "category": "D. Compliance & Certifications", "severity": "blocker",
     "title": "Required certifications cover this service+feature",
     "guidance": "SOC 2, ISO 27001/27018, etc. - confirm the specific service AND feature are in audit scope.",
     "azure": "Check Azure compliance offerings scope for the service.",
     "gcp": "Check GCP compliance reports manager for Vertex AI scope."},
    {"id": "D2", "category": "D. Compliance & Certifications", "severity": "blocker",
     "title": "HIPAA / BAA coverage (if PHI possible)",
     "guidance": "If any calling workload could carry PHI, confirm the service is covered under the BAA. N/A if PHI is impossible."},
    {"id": "D3", "category": "D. Compliance & Certifications", "severity": "required",
     "title": "Other regulatory obligations assessed",
     "guidance": "FedRAMP, PCI, EU AI Act transparency/risk obligations - as applicable to the use case."},
    {"id": "D4", "category": "D. Compliance & Certifications", "severity": "required",
     "title": "Preview compliance gaps acknowledged",
     "guidance": "If enabling a preview feature anyway, the compliance gap must be explicit in the decision conditions."},

    # E. Legal, Licensing & IP
    {"id": "E1", "category": "E. Legal, Licensing & IP", "severity": "blocker",
     "title": "Model license / terms of use reviewed",
     "guidance": "Open-weight licenses (Llama, Gemma) carry use restrictions; partner models have their own terms."},
    {"id": "E2", "category": "E. Legal, Licensing & IP", "severity": "required",
     "title": "Acceptable-use policy vs intended use",
     "guidance": "Confirm the planned use case is allowed by the CSP's AI acceptable-use policy / code of conduct."},
    {"id": "E3", "category": "E. Legal, Licensing & IP", "severity": "required",
     "title": "IP indemnification conditions understood",
     "guidance": "Indemnity usually has conditions - typically that required content filters/safety settings remain enabled.",
     "azure": "Customer Copyright Commitment: requires specified mitigations (e.g., content filters) to stay on.",
     "gcp": "Google's generative AI indemnity: conditions include not disabling safety filters."},
    {"id": "E4", "category": "E. Legal, Licensing & IP", "severity": "required",
     "title": "Preview/beta supplemental terms reviewed",
     "guidance": "Preview terms often weaken commitments (support, data handling, availability)."},

    # F. Security & Network
    {"id": "F1", "category": "F. Security & Network", "severity": "blocker",
     "title": "Private connectivity available and planned",
     "guidance": "Public network access per policy; private path for the LiteLLM proxy.",
     "azure": "Private Endpoint support for the account/feature; disable public network access if policy requires.",
     "gcp": "Private Service Connect / private endpoints for Vertex AI."},
    {"id": "F2", "category": "F. Security & Network", "severity": "required",
     "title": "Encryption requirements met",
     "guidance": "TLS in transit (default); customer-managed keys (CMK/CMEK) supported and configured if policy requires."},
    {"id": "F3", "category": "F. Security & Network", "severity": "blocker",
     "title": "Authentication method per policy",
     "guidance": "Prefer identity-based auth over static API keys.",
     "azure": "Entra ID auth; disable local auth (API keys) where possible.",
     "gcp": "IAM / service account auth; avoid API keys."},
    {"id": "F4", "category": "F. Security & Network", "severity": "required",
     "title": "Data exfiltration controls evaluated",
     "guidance": "Can the service be constrained to prevent exfiltration paths?",
     "azure": "Network rules / outbound restrictions.",
     "gcp": "VPC Service Controls perimeter support for the API."},
    {"id": "F5", "category": "F. Security & Network", "severity": "required",
     "title": "New attack surface reviewed",
     "guidance": "Tool use, agents, code execution, grounding-with-search send data to additional services and create new injection paths. Review what the feature can reach."},

    # G. Identity & Access
    {"id": "G1", "category": "G. Identity & Access", "severity": "required",
     "title": "RBAC model defined",
     "guidance": "Who can deploy/manage the resource vs who/what can invoke the model."},
    {"id": "G2", "category": "G. Identity & Access", "severity": "required",
     "title": "Least-privilege identity for the LiteLLM proxy",
     "guidance": "Managed identity / workload identity preferred over static keys; scope to invoke-only."},
    {"id": "G3", "category": "G. Identity & Access", "severity": "required",
     "title": "Secrets rotation and storage",
     "guidance": "If keys are unavoidable: stored in Key Vault / Secret Manager, rotation owner and cadence assigned."},

    # H. Responsible AI & Content Safety
    {"id": "H1", "category": "H. Responsible AI & Content Safety", "severity": "blocker",
     "title": "Content filtering configured per policy",
     "guidance": "Decide and document filter levels; remember indemnity may depend on filters staying on (E3).",
     "azure": "Azure AI Content Safety filter configuration on the deployment.",
     "gcp": "Vertex safety filter thresholds per harm category."},
    {"id": "H2", "category": "H. Responsible AI & Content Safety", "severity": "required",
     "title": "Abuse monitoring posture decided",
     "guidance": "Trade-off between CSP abuse detection and prompt retention/privacy (see C4)."},
    {"id": "H3", "category": "H. Responsible AI & Content Safety", "severity": "recommended",
     "title": "Prompt-injection / jailbreak protections",
     "guidance": "For user-facing or tool-using workloads.",
     "azure": "Prompt Shields.",
     "gcp": "Model Armor."},
    {"id": "H4", "category": "H. Responsible AI & Content Safety", "severity": "required",
     "title": "RAI assessment for new modalities",
     "guidance": "Image/video/voice generation introduces impersonation and deepfake risk - run the internal RAI assessment. N/A for text-only models already covered."},
    {"id": "H5", "category": "H. Responsible AI & Content Safety", "severity": "recommended",
     "title": "Grounding / hallucination risk vs use case",
     "guidance": "Does the use case need citations/grounding? Is the model's accuracy adequate for the decision it supports?"},

    # I. Observability & Audit
    {"id": "I1", "category": "I. Observability & Audit", "severity": "required",
     "title": "Audit/diagnostic logs enabled and shipped",
     "guidance": "Logs flow to the SIEM / central workspace.",
     "azure": "Diagnostic settings on the resource.",
     "gcp": "Cloud Audit Logs (admin + data access as needed)."},
    {"id": "I2", "category": "I. Observability & Audit", "severity": "required",
     "title": "Usage metrics on existing dashboards",
     "guidance": "Tokens, latency, error rate per model visible alongside existing models."},
    {"id": "I3", "category": "I. Observability & Audit", "severity": "required",
     "title": "Request/response logging policy decided",
     "guidance": "Logging prompts has PII implications; align LiteLLM logging config with the data classification (A4)."},
    {"id": "I4", "category": "I. Observability & Audit", "severity": "recommended",
     "title": "Alerting on throttling / quota exhaustion",
     "guidance": "429 rates and quota consumption alerts before developers notice."},

    # J. Quota & Capacity
    {"id": "J1", "category": "J. Quota & Capacity", "severity": "blocker",
     "title": "Default quotas sufficient for forecast load",
     "guidance": "TPM/RPM per region documented vs expected traffic. If insufficient, this blocks until J2 has a plan."},
    {"id": "J2", "category": "J. Quota & Capacity", "severity": "required",
     "title": "Quota increase path / provisioned throughput evaluated",
     "guidance": "Lead time for increases; PTU (Azure) / Provisioned Throughput (GCP) if guaranteed capacity is needed."},
    {"id": "J3", "category": "J. Quota & Capacity", "severity": "required",
     "title": "Enough regions for the load-balancing/HA strategy",
     "guidance": "LiteLLM balances across deployments - confirm the model is available in enough regions to meet HA requirements."},
    {"id": "J4", "category": "J. Quota & Capacity", "severity": "required",
     "title": "Throttling behavior tested with LiteLLM",
     "guidance": "Verify retry/fallback/cooldown config handles this model's 429 and error semantics."},

    # K. Cost & Commercial
    {"id": "K1", "category": "K. Cost & Commercial", "severity": "required",
     "title": "Pricing model understood, unit costs recorded",
     "guidance": "Input/output/cached token rates, image/audio pricing, reasoning tokens, PTU hourly - record numbers in notes or attach the pricing sheet."},
    {"id": "K2", "category": "K. Cost & Commercial", "severity": "recommended",
     "title": "Cost comparison vs already-enabled equivalents",
     "guidance": "Is an existing enabled model materially cheaper for the same capability?"},
    {"id": "K3", "category": "K. Cost & Commercial", "severity": "required",
     "title": "Budgets, alerts, chargeback tagging",
     "guidance": "Cost attribution to the requesting team; alerts on anomalous spend."},
    {"id": "K4", "category": "K. Cost & Commercial", "severity": "recommended",
     "title": "Hidden cost drivers reviewed",
     "guidance": "Long context windows, reasoning tokens, multimodal inputs can multiply effective cost."},

    # L. Platform Integration (LiteLLM)
    {"id": "L1", "category": "L. Platform Integration (LiteLLM)", "severity": "blocker",
     "title": "LiteLLM supports the provider/model and required features",
     "guidance": "Provider route, API version, and feature passthrough (streaming, function calling, vision, structured output) verified - ideally with a smoke test."},
    {"id": "L2", "category": "L. Platform Integration (LiteLLM)", "severity": "required",
     "title": "Model alias and routing strategy defined",
     "guidance": "Alias exposed to developers, deployment weights, fallback chain, cooldown settings."},
    {"id": "L3", "category": "L. Platform Integration (LiteLLM)", "severity": "recommended",
     "title": "Cross-CSP feature parity documented",
     "guidance": "If the same model class is served from multiple clouds, note behavioral differences developers will see."},
    {"id": "L4", "category": "L. Platform Integration (LiteLLM)", "severity": "recommended",
     "title": "Latency/load benchmark vs requirements",
     "guidance": "P50/P95 latency and throughput under representative load."},

    # M. Rollout & Rollback
    {"id": "M1", "category": "M. Rollout & Rollback", "severity": "required",
     "title": "Enablement plan",
     "guidance": "Which environments, which teams, gradual rollout vs all-at-once."},
    {"id": "M2", "category": "M. Rollout & Rollback", "severity": "required",
     "title": "Rollback plan",
     "guidance": "How to disable quickly: remove from LiteLLM config, delete deployments, revoke access."},
    {"id": "M3", "category": "M. Rollout & Rollback", "severity": "recommended",
     "title": "Developer communication prepared",
     "guidance": "Docs/announcement: model card link, alias name, known limitations, cost guidance."},
    {"id": "M4", "category": "M. Rollout & Rollback", "severity": "required",
     "title": "Re-review date and owner assigned",
     "guidance": "Especially for previews, conditional approvals, and models with retirement dates (B2)."},
]

CATEGORIES = []
for _item in ITEMS:
    if _item["category"] not in CATEGORIES:
        CATEGORIES.append(_item["category"])


def new_checklist():
    """Fresh response set embedded into a review at creation time."""
    return {
        item["id"]: {"status": "unreviewed", "notes": "", "evidence": ""}
        for item in ITEMS
    }
