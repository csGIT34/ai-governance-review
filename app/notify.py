"""Teams notifications through a channel's incoming webhook (Teams "Workflows" webhook,
which accepts an Adaptive Card message). Optional: does nothing unless TEAMS_WEBHOOK_URL
is set.

Notifications are sent after the change is committed (FastAPI BackgroundTasks) and never
raise: a Teams outage must not block saving audit evidence.
"""
import logging

import httpx

from app import config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.TEAMS_WEBHOOK_URL)


def card(title: str, text: str = "", facts: list[tuple[str, str]] | None = None,
         path: str = "") -> dict:
    body = [{"type": "TextBlock", "text": title, "weight": "Bolder", "size": "Medium", "wrap": True}]
    if text:
        body.append({"type": "TextBlock", "text": text, "wrap": True})
    if facts:
        body.append({"type": "FactSet", "facts": [{"title": k, "value": v} for k, v in facts]})
    content = {"type": "AdaptiveCard", "version": "1.4", "body": body,
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json"}
    if path and config.APP_BASE_URL:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "Open",
                               "url": config.APP_BASE_URL.rstrip("/") + path}]
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": content}]}


def _post(payload: dict):
    httpx.post(config.TEAMS_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()


def send(title: str, text: str = "", facts: list[tuple[str, str]] | None = None, path: str = ""):
    if not enabled():
        return
    try:
        _post(card(title, text, facts, path))
    except Exception as err:  # never let notifications break the request
        logger.warning("Teams notification failed (%s): %s", title, err)
