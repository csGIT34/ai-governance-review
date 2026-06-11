# AI Governance Review

Internal tool for the platform engineering team to review new AI models and features on
**Azure** and **GCP** before enabling them on the LiteLLM-based AI platform. Every review is a
structured 14-category checklist with evidence, a point-in-time CSP catalog snapshot, uploaded
artifacts, and a recorded decision — stored durably as the historical record.

See [docs/DESIGN.md](docs/DESIGN.md) for the architecture and the full review framework.

## Run locally

Prerequisites: Docker Desktop (Linux containers).

```sh
docker compose up --build
```

Then open **http://localhost:8000**. The Cosmos DB emulator can take ~30–60s on first start;
the app retries until it's reachable.

Also available:
- Cosmos Data Explorer: http://localhost:1234
- Azurite blob endpoint: http://localhost:10000

Data persists in the `azurite-data` volume (artifacts). The Cosmos emulator (vnext-preview) is
**not** persistent across container recreation — fine for the POC; reviews survive restarts but
not `docker compose down`. Use `docker compose stop`/`start` to keep data.

## Workflow

1. **New review** — capture CSP, service, model/feature, regions, requester, justification.
2. **Fetch catalog snapshot** — pulls lifecycle status, deprecation dates, capabilities from the
   Azure model catalog or Vertex AI publisher catalog and stores them on the review (optional,
   needs credentials — see below).
3. **Work the checklist** — 14 categories, ~45 items, each Pass / Fail / N/A / Needs info with
   notes and evidence links. CSP-specific guidance is shown per item.
4. **Upload artifacts** — screenshots, pricing sheets, exported terms (stored in Blob Storage).
5. **Record decision** — Approved / Approved with conditions / Rejected. Unconditional approval
   is blocked while any *blocker* item is not Pass/N-A.
6. **Export** — full review as JSON for audits.

## Optional: CSP catalog enrichment credentials

The app works fully without these; the enrich button will just explain it's not configured.

**Azure** — set in your shell (or a `.env` file next to `docker-compose.yml`) before
`docker compose up`:

```
AZURE_SUBSCRIPTION_ID=...
AZURE_TENANT_ID=...
AZURE_CLIENT_ID=...        # service principal with Reader on the subscription
AZURE_CLIENT_SECRET=...
```

On Azure Container Apps later, drop the SP variables and use the app's **managed identity** —
`DefaultAzureCredential` picks it up automatically.

**GCP** — run `gcloud auth application-default login` on your machine, then uncomment the
`GOOGLE_APPLICATION_CREDENTIALS` env var and the ADC volume mount in `docker-compose.yml`, and
set `GOOGLE_CLOUD_PROJECT` (used as the quota/billing project).

## Deploying to Azure (later)

The app is a single container; the local emulators map 1:1 to real services:

| Local (compose) | Azure |
|---|---|
| `app` container | Azure Container Apps |
| Cosmos DB emulator | Azure Cosmos DB (NoSQL), database `governance`, container `reviews`, PK `/csp` |
| Azurite | Storage Account, blob container `artifacts` |

Set `COSMOS_ENDPOINT`, `COSMOS_KEY`, and `BLOB_CONNECTION_STRING` on the Container App and it
runs unchanged. Add Entra ID authentication via Container Apps built-in auth (Easy Auth) before
exposing it to the team.
