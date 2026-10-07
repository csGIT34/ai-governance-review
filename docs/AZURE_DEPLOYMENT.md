# Azure deployment spec

What to provision for Governance Review on **Azure Container Apps (private) + Azure Database
for PostgreSQL Flexible Server + Storage account**, and how to wire and verify it.

This is a **specification, not Terraform**: map each item onto your organisation's Terraform
pattern modules. (Rule for whoever implements it: ask the owner before writing any `.tf`.)
Names below are placeholders (`<env>`, `<region>`); use your naming standard.

## Target architecture

```
                 corp network / VPN / ExpressRoute
                              │  (private DNS: *.internal env domain)
┌──────────── VNet ───────────┼──────────────────────────────────────────────┐
│ snet-aca (/27+, delegated Microsoft.App/environments)                        │
│   Container Apps environment (INTERNAL, workload profiles: Consumption)      │
│     ca-governance        app, Easy Auth (Entra ID), min 1 replica           │
│     caj-governance-migrate     manual job: alembic upgrade head + seed      │
│     caj-governance-due-issues  scheduled job: Teams digest (optional)       │
│ snet-pe                                                                      │
│   pe-blob ─► Storage account (shared keys OFF, public access OFF)            │
│   pe-acr  ─► Container registry (Premium)                                    │
│   pe-kv   ─► Key Vault (optional: webhook URL, GitHub token, auth secret)    │
│   pe-pg   ─► PostgreSQL Flexible Server (or VNet-integrated, see below)      │
└──────────────────────────────────────────────────────────────────────────────┘
   egress (firewall / NSG allow-list) ─► login.microsoftonline.com, management.azure.com,
                                         GitHub Enterprise API, Teams webhook host
```

Everything authenticates with **managed identities**: no storage keys, no database passwords.

## 1. Identities

| Identity | Type | Used by | Why separate |
|---|---|---|---|
| `id-governance-app` | user-assigned | the Container App, due-issues job | Runtime: data read/write (DML only on the database) |
| `id-governance-migrate` | user-assigned | the migration job | Owns the schema (DDL). Keeping it separate lets the app's role be denied UPDATE/DELETE on `audit_events` |

User-assigned (not system-assigned) so the role assignments and Postgres roles exist before
the first deployment. Set `AZURE_CLIENT_ID` to the identity's **client id** in each
workload; `DefaultAzureCredential` needs it to pick a user-assigned identity.

## 2. Role assignments

| Principal | Role | Scope | For |
|---|---|---|---|
| `id-governance-app`, `id-governance-migrate` | AcrPull | container registry | pulling the image |
| `id-governance-app` | Storage Blob Data Contributor | storage account, or the `artifacts` container | evidence files |
| `id-governance-app` | Reader | the management group you assess (e.g. tenant root group) | scope sync (Management Groups API) and Azure evidence (Resource Graph). Management Group Reader alone covers the sync but not Resource Graph |
| `id-governance-app` | Key Vault Secrets User | Key Vault (if used) | secret references |
| `id-governance-app` | Reader | subscription(s) with Azure OpenAI / AI Foundry | AI review catalog enrichment (optional; covered by the MG-level Reader if those subscriptions are under it) |

## 3. Networking (private)

- **VNet** with:
  - `snet-aca`: at least /27, delegated to `Microsoft.App/environments` (workload profiles environment).
  - `snet-pe`: for private endpoints.
  - Optional `snet-pg`: /28+, delegated to `Microsoft.DBforPostgreSQL/flexibleServers`, if you
    use VNet integration for Postgres instead of a private endpoint (follow your pattern module).
- **Container Apps environment**: internal (`vnetConfiguration.internal = true`), workload
  profile `Consumption`, logs to Log Analytics.
- **Private DNS zones**, linked to the VNet and to wherever users' DNS resolves from:

  | Zone | Records |
  |---|---|
  | `<environment default domain>` (e.g. `<unique>.<region>.azurecontainerapps.io`) | `*` → environment static IP |
  | `privatelink.blob.core.windows.net` | storage private endpoint |
  | `privatelink.azurecr.io` | registry private endpoint |
  | `privatelink.vaultcore.azure.net` | Key Vault (if used) |
  | `privatelink.postgres.database.azure.com` (private endpoint) or `<server>.private.postgres.database.azure.com` (VNet integration) | Postgres |

- **Egress allow-list** for the Container Apps subnet (firewall / UDR / NSG):

  | Destination | Needed for |
  |---|---|
  | `login.microsoftonline.com`, `login.windows.net` | Easy Auth (OIDC metadata, token validation) |
  | `management.azure.com` | scope sync, Resource Graph evidence, AI catalog |
  | your GitHub Enterprise API host | evidence-link pinning (if `GITHUB_TOKEN` set) |
  | `*.logic.azure.com` / `*.environment.api.powerplatform.com` | Teams Workflows webhook (if used) |
  | `learn.microsoft.com`, `azure.microsoft.com`, `www.microsoft.com`, Google terms pages | AI review reference-doc snapshots (optional; the feature reports errors without them) |
  | `aiplatform.googleapis.com` | AI review GCP enrichment (optional) |

  Managed identity tokens come from the platform's local identity endpoint, so they need no
  egress.

## 4. Container registry

Premium SKU (required for a private endpoint), admin user disabled, public network access
disabled, AcrPull for both identities. A shared corporate ACR works the same way.

## 5. Storage account (evidence files)

| Setting | Value |
|---|---|
| Kind / redundancy | StorageV2, ZRS (or GRS per your DR policy) |
| `allowSharedKeyAccess` | **false**: the app uses Entra auth only (`BLOB_ACCOUNT_URL`) |
| `allowBlobPublicAccess` | false |
| `publicNetworkAccess` | Disabled (private endpoint `blob`) |
| Minimum TLS | 1.2 |
| Blob versioning | on |
| Soft delete | blobs and containers, ≥ 30 days |
| Container | `artifacts` (private) |
| Immutability | time-based retention policy on `artifacts`, retention = your evidence retention period (e.g. 7 years). Create it **unlocked**, verify uploads work, then lock it |

The app only ever creates new blobs (never overwrites or deletes), so a locked policy is
compatible. It tries to create the container at start and tolerates 403/409, so the IaC must
create it.

## 6. PostgreSQL Flexible Server

| Setting | Value |
|---|---|
| Version | 16 |
| SKU | Burstable B2s is enough for a small team (General Purpose D2ds_v5 if you need the SLA/HA) |
| Storage | 32 GiB, auto-grow on |
| Backup | retention 35 days; geo-redundant if your DR policy needs it |
| High availability | per policy (zone-redundant if required) |
| Authentication | **Microsoft Entra only** (password auth disabled); Entra admin = a DBA group |
| Network | private (private endpoint or VNet integration), public access disabled |
| TLS | `require_secure_transport` on (default) |
| Database | `governance` |

### Database roles (one-time SQL, as the Entra admin, connected to `governance`)

```sql
-- 1. Before the first migration
SELECT * FROM pgaadauth_create_principal('id-governance-migrate', false, false);
SELECT * FROM pgaadauth_create_principal('id-governance-app', false, false);
GRANT CREATE, USAGE ON SCHEMA public TO "id-governance-migrate";

-- 2. After the first successful migration job (tables are owned by id-governance-migrate)
GRANT USAGE ON SCHEMA public TO "id-governance-app";
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "id-governance-app";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "id-governance-app";
REVOKE UPDATE, DELETE, TRUNCATE ON audit_events FROM "id-governance-app";
-- future tables from later migrations
ALTER DEFAULT PRIVILEGES FOR ROLE "id-governance-migrate" IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "id-governance-app";
ALTER DEFAULT PRIVILEGES FOR ROLE "id-governance-migrate" IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO "id-governance-app";
```

The migration also installs triggers that reject UPDATE/DELETE/TRUNCATE on `audit_events`
for every role, including the owner.

## 7. Entra ID app registration (Easy Auth)

- App registration `governance-review-<env>`: web redirect URI
  `https://<app fqdn>/.auth/login/aad/callback`, ID tokens enabled, sign-in audience = your tenant.
- Enterprise application: **Assignment required = yes**; assign the security group(s) of people
  who may use the app.
- Client secret, or a federated credential if your pattern supports it, stored in Key Vault
  and referenced by the Container App secret `microsoft-provider-authentication-secret`.

## 8. Container App `ca-governance`

| Setting | Value |
|---|---|
| Image | `<acr>.azurecr.io/governance-review:<git sha>` (pulled with `id-governance-app`) |
| Identity | user-assigned `id-governance-app` |
| Ingress | enabled, target port **8000**, transport auto, "external" within the internal environment (reachable from the VNet/corp network only), HTTPS only |
| Scale | min 1, max 1 (raise max later; migrations are already in a job) |
| Resources | 1 vCPU / 2 GiB (Chromium for AI reference-doc PDFs needs the memory) |
| Probes | liveness `GET /health`, readiness `GET /health/ready` (checks the database), startup `GET /health` |
| Auth (authConfigs) | platform enabled; identity provider `azureActiveDirectory` with the registration's client id, secret setting `microsoft-provider-authentication-secret`, issuer `https://login.microsoftonline.com/<tenant id>/v2.0`; `globalValidation.unauthenticatedClientAction = RedirectToLoginPage`, `redirectToProvider = azureactivedirectory`, **`excludedPaths = ["/health", "/health/ready"]`** |

If your azurerm version can't express Container Apps auth, use
`azapi_resource` for `Microsoft.App/containerApps/authConfigs`.

> The app trusts the `X-MS-CLIENT-PRINCIPAL-*` headers. Never expose it without Easy Auth,
> and never set `AUTH_MODE=dev` outside a laptop.

### Environment variables

| Variable | Value |
|---|---|
| `AUTH_MODE` | `easyauth` |
| `ADMIN_UPNS` | first admin(s), e.g. `you@corp.com` |
| `DEFAULT_ROLE` | `viewer` |
| `DATABASE_URL` | `postgresql+psycopg://id-governance-app@<server>.postgres.database.azure.com:5432/governance?sslmode=require` |
| `DATABASE_AUTH` | `entra` |
| `AZURE_CLIENT_ID` | client id of `id-governance-app` |
| `BLOB_ACCOUNT_URL` | `https://<account>.blob.core.windows.net` |
| `BLOB_CONTAINER` | `artifacts` |
| `RUN_MIGRATIONS` | `false` (the job does it) |
| `AZURE_TENANT_ID` | tenant id (scope sync root = tenant root group) |
| `AZURE_ROOT_MANAGEMENT_GROUP` | optional: sync a lower management group instead |
| `EVIDENCE_REPO_BASE` | `https://<ghe host>/<org>/<governance repo>` |
| `GITHUB_API_URL` | `https://<ghe host>/api/v3` |
| `GITHUB_TOKEN` | secret (Key Vault ref): read-only token, Contents: read on the evidence repo (GitHub App installation token or fine-grained PAT) |
| `TEAMS_WEBHOOK_URL` | secret (Key Vault ref), optional |
| `APP_BASE_URL` | `https://<app fqdn>` (links in Teams messages) |
| `MAX_UPLOAD_MB` | `50` |
| `AZURE_SUBSCRIPTION_ID`, `GOOGLE_CLOUD_PROJECT` | only for AI review catalog enrichment |

## 9. Jobs

| Job | Trigger | Command | Identity / env |
|---|---|---|---|
| `caj-governance-migrate` | Manual (started by the pipeline before each rollout) | `sh -c "alembic upgrade head && python -m app.seed"` | `id-governance-migrate`; `DATABASE_URL` with user `id-governance-migrate`, `DATABASE_AUTH=entra`, `AZURE_CLIENT_ID` of that identity |
| `caj-governance-due-issues` | Schedule, e.g. `0 7 * * 1-5` | `python -m app.jobs due-issues` | `id-governance-app`; same env as the app (needs `TEAMS_WEBHOOK_URL`) |

Both use the same image tag as the app. Replica timeout 600s and retry limit 1 are enough.

## 10. Rollout order

1. Provision everything above (Terraform via your pattern modules).
2. Run SQL step 1 (Postgres roles).
3. Build and push the image: `docker build -t <acr>.azurecr.io/governance-review:<sha> .`
4. Start `caj-governance-migrate` and wait for success.
5. Run SQL step 2 (grants) - first deployment only.
6. Deploy the Container App revision with the new image.
7. Sign in as an `ADMIN_UPNS` user → **Users** → give reviewers/preparers their roles.
8. **Libraries** → create the controls library (paste/import from the control document).
9. **Scope → Sync from Azure.**

Every later release: build → push → migration job → new revision.

## 11. Verification checklist

- [ ] `https://<app fqdn>` from the corp network → Entra sign-in → app loads; from outside → unreachable.
- [ ] `/health/ready` returns `{"status":"ok"}` (from inside the VNet).
- [ ] Sign-in as a user not assigned to the enterprise app is refused.
- [ ] Upload a file on an answer → blob appears under `artifacts/assessments/<id>/…`; downloading works.
- [ ] Storage account: shared key access disabled, public access disabled, immutability policy present (locked once verified).
- [ ] Scope → Sync from Azure lists your management groups and subscriptions.
- [ ] A control with an Azure evidence query attaches a JSON result.
- [ ] A branch link to the evidence repo is pinned to a commit (if `GITHUB_TOKEN` set).
- [ ] As `id-governance-app`: `UPDATE audit_events SET note = 'x'` fails (privileges + trigger).
- [ ] Postgres: password authentication disabled; only Entra principals can connect.
- [ ] Container logs reach Log Analytics; no secrets appear in logs.
- [ ] Excel export of an assessment opens and includes the Audit log sheet.
