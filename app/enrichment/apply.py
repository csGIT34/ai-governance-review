"""Map catalog snapshot facts onto checklist items.

Only items still 'unreviewed' with empty notes are touched, so reviewer
input is never overwritten. Auto-filled notes are prefixed [auto] for provenance and the
evidence field points at the snapshot source.

Coverage: A2 (exact identity), B1 (GA vs preview), B2 (deprecation dates,
Azure only - the Vertex catalog does not expose them). Everything else
stays a human judgment.
"""


def _set(doc: dict, item_id: str, status: str, note: str, source: str):
    resp = doc["checklist"].get(item_id)
    if not resp or resp["status"] != "unreviewed" or resp["notes"]:
        return None
    resp["status"] = status
    resp["notes"] = f"[auto] {note}"
    resp["evidence"] = source
    return {"item": item_id, "status": status, "note": note}


def apply_azure(snapshot: dict, doc: dict) -> list[dict]:
    source = snapshot.get("source", "Azure model catalog")
    name = doc["model_name"].strip().lower()
    version = doc.get("model_version", "").strip()
    exact = [m for m in snapshot.get("models") or []
             if (m.get("name") or "").lower() == name]
    if version:
        exact = [m for m in exact if m.get("version") == version]

    applied = []
    if not exact:
        wanted = f"'{doc['model_name']}'" + (f" version {version}" if version else "")
        applied.append(_set(doc, "A2", "needs_info",
                            f"no exact catalog match for {wanted}; check the model id",
                            source))
        return [a for a in applied if a]

    if len(exact) == 1:
        m = exact[0]
        applied.append(_set(doc, "A2", "pass",
                            f"catalog match: {m['name']} version {m['version']}", source))
    else:
        versions = ", ".join(m.get("version") or "?" for m in exact)
        applied.append(_set(doc, "A2", "needs_info",
                            f"multiple versions in catalog ({versions}); pin one", source))

    lifecycles = {m.get("lifecycle_status") for m in exact}
    if lifecycles == {"GenerallyAvailable"}:
        applied.append(_set(doc, "B1", "pass", "GenerallyAvailable per catalog", source))
    elif lifecycles:
        applied.append(_set(doc, "B1", "needs_info",
                            f"lifecycle status: {', '.join(sorted(s or '?' for s in lifecycles))}"
                            " - preview policy decision required", source))

    retire_dates = sorted({(m.get("deprecation") or {}).get("inference")
                           for m in exact
                           if (m.get("deprecation") or {}).get("inference")})
    if retire_dates:
        applied.append(_set(doc, "B2", "pass",
                            f"inference retirement {retire_dates[0][:10]} per catalog"
                            " - set the re-review date before this", source))

    return [a for a in applied if a]


def apply_gcp(snapshot: dict, doc: dict) -> list[dict]:
    source = snapshot.get("source", "Vertex AI publisher catalog")
    model = snapshot.get("model") or {}
    if not model:
        return []

    applied = []
    if model.get("version_id"):
        applied.append(_set(doc, "A2", "pass",
                            f"catalog match: {model.get('name')} version {model['version_id']}",
                            source))

    stage = model.get("launch_stage")
    if stage == "GA":
        applied.append(_set(doc, "B1", "pass", "launch stage GA per catalog", source))
    elif stage:
        applied.append(_set(doc, "B1", "needs_info",
                            f"launch stage {stage} - preview policy decision required", source))

    return [a for a in applied if a]
