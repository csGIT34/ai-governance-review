"""Pull model metadata from the Azure AI (Cognitive Services) model catalog.

ARM endpoint: subscriptions/{sub}/providers/Microsoft.CognitiveServices/
locations/{region}/models — returns versions, lifecycle status, deprecation
dates, capabilities, and SKUs per model per region.

Requires AZURE_SUBSCRIPTION_ID and credentials resolvable by
DefaultAzureCredential (env service principal locally, managed identity on
Azure Container Apps).
"""
from datetime import datetime, timezone

import httpx

from app import config

API_VERSION = "2024-10-01"


def enrich(model_name: str, region: str) -> dict:
    if not config.AZURE_SUBSCRIPTION_ID:
        return {"error": "AZURE_SUBSCRIPTION_ID is not set; Azure enrichment is disabled. "
                         "Fill in the checklist manually or configure credentials (see README)."}
    try:
        from azure.identity import DefaultAzureCredential
        token = DefaultAzureCredential().get_token("https://management.azure.com/.default").token
    except Exception as err:
        return {"error": f"Could not acquire Azure credentials: {err}"}

    url = (
        f"https://management.azure.com/subscriptions/{config.AZURE_SUBSCRIPTION_ID}"
        f"/providers/Microsoft.CognitiveServices/locations/{region}/models"
    )
    try:
        resp = httpx.get(
            url,
            params={"api-version": API_VERSION},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as err:
        return {"error": f"Azure model catalog request failed: {err}"}

    needle = model_name.lower().strip()
    matches = []
    for entry in resp.json().get("value", []):
        model = entry.get("model", {})
        if needle in model.get("name", "").lower():
            matches.append({
                "name": model.get("name"),
                "version": model.get("version"),
                "format": model.get("format"),
                "kind": entry.get("kind"),
                "lifecycle_status": model.get("lifecycleStatus"),
                "deprecation": model.get("deprecation"),
                "max_capacity": model.get("maxCapacity"),
                "capabilities": model.get("capabilities"),
                "skus": [s.get("name") for s in model.get("skus", [])],
            })

    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": f"Azure model catalog ({region}, api-version {API_VERSION})",
        "query": model_name,
        "match_count": len(matches),
        "models": matches,
    }
