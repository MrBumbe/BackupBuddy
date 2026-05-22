"""
SMTP notification channel for the gatekeeper.

Sends email via aiosmtplib.  TLS is always required:
  - port 587 → STARTTLS
  - port 465 → implicit TLS (SMTP_SSL)
  - all other ports → STARTTLS attempted

Password is fetched from the encrypted secrets store under the key
"smtp_password".  Never logged, never stored in plaintext.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import aiosmtplib

if TYPE_CHECKING:
    from gatekeeper.config import NotifySmtpConfig
    from gatekeeper.secrets import SecretsStore

logger = logging.getLogger(__name__)

_SECRETS_KEY = "smtp_password"
_TIMEOUT = 15  # seconds


# ── Internal helper ───────────────────────────────────────────────────────────

def _build_email(
    config: "NotifySmtpConfig",
    node_name: str,
    level: str,
    message: str,
    detail: str | None,
) -> str:
    """Build a plain-text RFC 2822 email string."""
    subject = f"[BackupBuddy/{node_name}] {level.upper()}: {message}"
    body_lines = [message]
    if detail:
        body_lines += ["", detail]

    return (
        f"From: BackupBuddy <{config.user}>\r\n"
        f"To: {config.to}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        + "\r\n".join(body_lines)
    )


async def _resolve_password(
    *,
    secrets: "SecretsStore | None",
    password: str | None,
) -> str:
    """Return password from explicit param, or from secrets store."""
    if password is not None:
        return password
    if secrets is None:
        raise ValueError("smtp: no password provided and no secrets store configured")
    return secrets.get_secret(_SECRETS_KEY)


# ── Public API ────────────────────────────────────────────────────────────────

async def send_smtp(
    config: "NotifySmtpConfig",
    node_name: str,
    level: str,
    message: str,
    detail: str | None,
    *,
    secrets: "SecretsStore | None" = None,
    password: str | None = None,
) -> None:
    """Send an alert email.

    Args:
        config:    SMTP configuration from gatekeeper.cfg.
        node_name: Gatekeeper node name (used in subject/from).
        level:     Alert level — info | warning | error | critical.
        message:   Human-readable alert message.
        detail:    Optional additional detail.
        secrets:   SecretsStore instance; used if *password* is not given.
        password:  Explicit password (used by test_smtp() before storing).

    Raises:
        aiosmtplib.SMTPException: on delivery failure.
        ValueError: if no password is available.
    """
    pw = await _resolve_password(secrets=secrets, password=password)
    email_body = _build_email(config, node_name, level, message, detail)

    use_tls = config.port == 465
    logger.info(
        "smtp: sending %s alert to %s via %s:%d",
        level, config.to, config.host, config.port,
    )
    try:
        if use_tls:
            await aiosmtplib.send(
                email_body,
                hostname=config.host,
                port=config.port,
                username=config.user,
                password=pw,
                use_tls=True,
                timeout=_TIMEOUT,
            )
        else:
            await aiosmtplib.send(
                email_body,
                hostname=config.host,
                port=config.port,
                username=config.user,
                password=pw,
                start_tls=True,
                timeout=_TIMEOUT,
            )
        logger.info("smtp: alert delivered to %s", config.to)
    except aiosmtplib.SMTPException:
        logger.error(
            "smtp: failed to deliver %s alert to %s via %s:%d",
            level, config.to, config.host, config.port,
        )
        raise


async def test_smtp(
    config: "NotifySmtpConfig",
    *,
    secrets: "SecretsStore | None" = None,
    password: str | None = None,
) -> bool:
    """Attempt to send a test email.  Returns True on success, False on failure.

    Accepts an explicit *password* so the Settings UI can test before storing.
    """
    try:
        await send_smtp(
            config,
            node_name="test",
            level="info",
            message="BackupBuddy SMTP test",
            detail="This is a test message from the gatekeeper settings.",
            secrets=secrets,
            password=password,
        )
        return True
    except Exception as exc:
        logger.warning("smtp: test failed — %s", type(exc).__name__)
        return False
