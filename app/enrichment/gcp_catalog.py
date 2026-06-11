"""Pull publisher model metadata from Vertex AI Model Garden.

Endpoint: publishers/{publisher}/models/{model} (v1beta1) — returns launch
stage (GA/Preview/Experimental), version, and supported actions.

Requires Application Default Credentials (mounted ADC file locally, workload
identity / service account on GCP — not needed since this app runs on Azure;
locally a mounted ADC json works).
"""
from datetime import datetime, timezone

import httpx

from app import config


def enrich(model_name: str, publisher: str = "google") -> dict:
    try:
        import google.auth
        from google.auth.transport.requests import Request

        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(Request())
        token = creds.token
    except Exception as err:
        return {"error": f"Could not acquire GCP credentials (Application Default "
                         f"Credentials): {err}. Fill in the checklist manually or "
                         f"configure ADC (see README)."}

    model_id = model_name.strip().lower()
    url = (
        f"https://aiplatform.googleapis.com/v1beta1/publishers/{publisher}"
        f"/models/{model_id}"
    )
    headers = {"Authorization": f"Bearer {token}"}
    if config.GOOGLE_CLOUD_PROJECT:
        headers["x-goog-user-project"] = config.GOOGLE_CLOUD_PROJECT
    try:
        resp = httpx.get(
            url,
            params={"view": "PUBLISHER_MODEL_VIEW_FULL"},
            headers=headers,
            timeout=30,
        )
        resp.raise_for_status()
    except httpx.HTTPStatusError as err:
        if err.response.status_code == 404:
            return {"error": f"Model '{publisher}/{model_id}' not found in the Vertex AI "
                             f"publisher catalog. Check the exact model id."}
        return {"error": f"Vertex AI catalog request failed: {err}"}
    except Exception as err:
        return {"error": f"Vertex AI catalog request failed: {err}"}

    data = resp.json()
    return {
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "source": f"Vertex AI publisher catalog (publishers/{publisher})",
        "query": model_name,
        "model": {
            "name": data.get("name"),
            "version_id": data.get("versionId"),
            "launch_stage": data.get("launchStage"),
            "open_source_category": data.get("openSourceCategory"),
            "supported_actions": list(data.get("supportedActions", {}).keys()),
            "publisher_model_template": data.get("publisherModelTemplate"),
        },
    }
