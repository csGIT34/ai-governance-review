"""Teams notifications through a channel's incoming webhook (Teams "Workflows" webhook,
which accepts an Adaptive Card message). Optional: does nothing unless TEAMS_WEBHOOK_URL
is set.

Notifications are sent after the change is committed (FastAPI BackgroundTasks) and never
raise: a Teams outage must not block saving audit evidence. The webhook URL is a secret
(it carries a signature), so it is never logged.
"""
import logging

import httpx

from app import config

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(config.TEAMS_WEBHOOK_URL)


def _plain(text: str, **style) -> dict:
    """User-supplied text as a RichTextBlock/TextRun: rendered literally, so markdown such
    as [label](url) in an issue title or comment can't become a link in a trusted message."""
    return {"type": "RichTextBlock", "inlines": [{"type": "TextRun", "text": text, **style}]}


def card(title: str, text: str = "", facts: list[tuple[str, str]] | None = None,
         path: str = "") -> dict:
    body = [_plain(title, weight="Bolder", size="Medium")]
    if text:
        body.append(_plain(text))
    for label, value in facts or []:
        body.append({"type": "RichTextBlock", "inlines": [
            {"type": "TextRun", "text": f"{label}: ", "weight": "Bolder"},
            {"type": "TextRun", "text": value}]})
    content = {"type": "AdaptiveCard", "version": "1.4", "body": body,
               "$schema": "http://adaptivecards.io/schemas/adaptive-card.json"}
    if path and config.APP_BASE_URL:
        content["actions"] = [{"type": "Action.OpenUrl", "title": "Open",
                               "url": config.APP_BASE_URL.rstrip("/") + path}]
    return {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": content}]}


def _post(payload: dict):
    httpx.post(config.TEAMS_WEBHOOK_URL, json=payload, timeout=10).raise_for_status()


def _describe(err: Exception) -> str:
    """Error summary without the URL (httpx error messages include it)."""
    if isinstance(err, httpx.HTTPStatusError):
        return f"HTTP {err.response.status_code}"
    return type(err).__name__


def send(title: str, text: str = "", facts: list[tuple[str, str]] | None = None, path: str = "",
         raise_errors: bool = False) -> bool:
    """Post a card. Returns True if sent. Request handlers keep the default (never raise);
    scheduled jobs pass raise_errors=True so a failed run shows as failed."""
    if not enabled():
        return False
    try:
        _post(card(title, text, facts, path))
        return True
    except Exception as err:
        logger.warning("Teams notification failed (%s): %s", title, _describe(err))
        if raise_errors:
            raise RuntimeError(f"Teams notification failed: {_describe(err)}") from None
        return False
