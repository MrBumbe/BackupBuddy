"""
Alert dispatcher for the gatekeeper.

Routes alerts to enabled notification channels (SMTP and/or webhook)
respecting per-event configuration flags.

Usage:
    dispatcher = AlertDispatcher(config, secrets_store, node_name)
    await dispatcher.send_alert("error", "Restore failed", event="backup_failure")

Event keys (map to notify.on_* in gatekeeper.cfg):
    "backup_success"  → notify.on_backup_success
    "backup_failure"  → notify.on_backup_failure
    "storage_warning" → notify.on_storage_warning
    "node_offline"    → notify.on_node_offline
    "rebalance"       → notify.on_rebalance

If event is None, the alert is always dispatched (no per-event filter).
Critical-level alerts always bypass the event filter.

Secrets store keys:
    "smtp_password"  — SMTP password
    "webhook_url"    — webhook endpoint URL
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from gatekeeper.notify import smtp as _smtp
from gatekeeper.notify import webhook as _webhook

if TYPE_CHECKING:
    from gatekeeper.config import GatekeeperConfig, NotifyConfig
    from gatekeeper.secrets import SecretsStore

logger = logging.getLogger(__name__)

VALID_LEVELS = frozenset({"info", "warning", "error", "critical"})

# Maps event key → attribute name on NotifyConfig
_EVENT_TO_CONFIG: dict[str, str] = {
    "backup_success":  "on_backup_success",
    "backup_failure":  "on_backup_failure",
    "storage_warning": "on_storage_warning",
    "node_offline":    "on_node_offline",
    "rebalance":       "on_rebalance",
}


# ── AlertDispatcher ───────────────────────────────────────────────────────────

class AlertDispatcher:
    """Routes alerts to configured notification channels.

    Args:
        notify_config: The [notify] section of GatekeeperConfig.
        secrets:       SecretsStore for retrieving smtp_password and webhook_url.
        node_name:     Gatekeeper node name (used in message headers/payloads).
    """

    def __init__(
        self,
        notify_config: "NotifyConfig",
        secrets: "SecretsStore",
        node_name: str,
    ) -> None:
        self._notify = notify_config
        self._secrets = secrets
        self._node_name = node_name

    async def send_alert(
        self,
        level: str,
        message: str,
        detail: str | None = None,
        *,
        event: str | None = None,
    ) -> None:
        """Dispatch an alert to all enabled channels.

        Args:
            level:   Severity — "info" | "warning" | "error" | "critical".
            message: Human-readable alert message (no secrets, no paths).
            detail:  Optional additional detail line.
            event:   Per-event key (see module docstring).  None = always send.
                     Critical level always bypasses the event filter.
        """
        if level not in VALID_LEVELS:
            logger.warning("dispatcher: unknown level %r — treating as 'error'", level)
            level = "error"

        if not self._should_dispatch(level, event):
            logger.debug(
                "dispatcher: suppressed %s alert (event=%r disabled in config)",
                level, event,
            )
            return

        tasks = []
        if self._notify.smtp.enabled:
            tasks.append(self._send_via_smtp(level, message, detail))
        if self._notify.webhook.enabled:
            tasks.append(self._send_via_webhook(level, message, detail))

        if not tasks:
            logger.debug("dispatcher: no channels enabled — alert not sent")
            return

        # Run both channels concurrently; failures in one do not cancel the other
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    "dispatcher: channel delivery failed — %s", type(result).__name__
                )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _should_dispatch(self, level: str, event: str | None) -> bool:
        """Return True if the alert should be dispatched.

        Critical alerts always go through.  Other alerts are filtered by the
        per-event config flag when an event key is provided.
        """
        if level == "critical":
            return True
        if event is None:
            return True
        config_attr = _EVENT_TO_CONFIG.get(event)
        if config_attr is None:
            # Unknown event key — dispatch to be safe
            logger.debug("dispatcher: unknown event key %r — dispatching anyway", event)
            return True
        return getattr(self._notify, config_attr, True)

    async def _send_via_smtp(
        self, level: str, message: str, detail: str | None
    ) -> None:
        await _smtp.send_smtp(
            self._notify.smtp,
            self._node_name,
            level,
            message,
            detail,
            secrets=self._secrets,
        )

    async def _send_via_webhook(
        self, level: str, message: str, detail: str | None
    ) -> None:
        await _webhook.send_webhook(
            self._node_name,
            level,
            message,
            detail,
            secrets=self._secrets,
        )
