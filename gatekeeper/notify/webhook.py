"""
Webhook notification channel for the gatekeeper.

Sends an HTTP POST with a generic JSON payload.  Compatible with Slack,
Discord, Ntfy, Gotify, and similar.

The webhook URL is fetched from the encrypted secrets store under the key
"webhook_url".  Never logged, never stored in plaintext.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from gatekeeper.secrets import SecretsStore

logger = logging.getLogger(__name__)

_SECRETS_KEY = "webhook_url"
_TIMEOUT = 5.0  # seconds — short: a dead webhook must not block alerts


# ── Internal helper ───────────────────────────────────────────────────────────

def _resolve_url(
    *,
    secrets: "SecretsStore | None",
    url: str | None,
) -> str:
    """Return URL from explicit param, or from secrets store."""
    if url is not None:
        return url
    if secrets is None:
        raise ValueError("webhook: no URL provided and no secrets store configured")
    return secrets.get_secret(_SECRETS_KEY)


def _build_payload(
    node_name: str,
    level: str,
    message: str,
    detail: str | None,
) -> dict:
    """Build the generic JSON payload."""
    payload: dict = {
        "level": level,
        "message": message,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "node": node_name,
    }
    if detail is not None:
        payload["detail"] = detail
    return payload


# ── Public API ────────────────────────────────────────────────────────────────

async def send_webhook(
    node_name: str,
    level: str,
    message: str,
    detail: str | None,
    *,
    secrets: "SecretsStore | None" = None,
    url: str | None = None,
) -> None:
    """POST an alert to the configured webhook endpoint.

    Args:
        node_name: Gatekeeper node name (included in payload).
        level:     Alert level — info | warning | error | critical.
        message:   Human-readable alert message.
        detail:    Optional additional detail.
        secrets:   SecretsStore instance; used if *url* is not given.
        url:       Explicit URL (used by test_webhook() before storing).

    Raises:
        httpx.HTTPError: on delivery failure.
        ValueError: if no URL is available.
    """
    resolved_url = _resolve_url(secrets=secrets, url=url)
    payload = _build_payload(node_name, level, message, detail)

    logger.info("webhook: sending %s alert", level)
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            response = await client.post(resolved_url, json=payload)
            response.raise_for_status()
        logger.info("webhook: alert delivered (status %s)", response.status_code)
    except httpx.HTTPStatusError as exc:
        logger.error(
            "webhook: %s alert rejected — HTTP %d",
            level, exc.response.status_code,
        )
        raise
    except httpx.HTTPError as exc:
        logger.error("webhook: %s alert failed — %s", level, type(exc).__name__)
        raise


async def test_webhook(
    *,
    secrets: "SecretsStore | None" = None,
    url: str | None = None,
    node_name: str = "test",
) -> bool:
    """POST a test payload to the webhook.  Returns True on success, False on failure.

    Accepts an explicit *url* so the Settings UI can test before storing.
    """
    try:
        await send_webhook(
            node_name=node_name,
            level="info",
            message="BackupBuddy webhook test",
            detail="This is a test message from the gatekeeper settings.",
            secrets=secrets,
            url=url,
        )
        return True
    except Exception as exc:
        logger.warning("webhook: test failed — %s", type(exc).__name__)
        return False
