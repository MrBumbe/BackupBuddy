"""
Unit tests for gatekeeper/notify/ — dispatcher, smtp, webhook.

All network calls are mocked.  No external services required.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatekeeper.config import NotifyConfig, NotifySmtpConfig, NotifyWebhookConfig
from gatekeeper.notify.dispatcher import AlertDispatcher, _EVENT_TO_CONFIG
from gatekeeper.notify import smtp as smtp_mod
from gatekeeper.notify import webhook as webhook_mod


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _smtp_config(**kw) -> NotifySmtpConfig:
    defaults = dict(enabled=True, host="smtp.example.com", port=587,
                    user="test@example.com", to="dest@example.com")
    return NotifySmtpConfig(**{**defaults, **kw})


def _webhook_config(enabled=True, **kw) -> NotifyWebhookConfig:
    return NotifyWebhookConfig(enabled=enabled, **kw)


def _notify_config(
    smtp_enabled=True,
    webhook_enabled=True,
    on_backup_success=False,
    on_backup_failure=True,
    on_storage_warning=True,
    on_node_offline=True,
    on_rebalance=True,
) -> NotifyConfig:
    return NotifyConfig(
        smtp=_smtp_config(enabled=smtp_enabled),
        webhook=_webhook_config(enabled=webhook_enabled),
        on_backup_success=on_backup_success,
        on_backup_failure=on_backup_failure,
        on_storage_warning=on_storage_warning,
        on_node_offline=on_node_offline,
        on_rebalance=on_rebalance,
    )


def _secrets_store(smtp_password="s3cr3t", webhook_url="https://example.com/hook"):
    store = MagicMock()
    def _get(key):
        if key == "smtp_password":
            return smtp_password
        if key == "webhook_url":
            return webhook_url
        raise KeyError(key)
    store.get_secret.side_effect = _get
    return store


def _dispatcher(
    smtp_enabled=True,
    webhook_enabled=True,
    on_backup_success=False,
    on_backup_failure=True,
    on_storage_warning=True,
    on_node_offline=True,
    on_rebalance=True,
) -> AlertDispatcher:
    return AlertDispatcher(
        _notify_config(
            smtp_enabled=smtp_enabled,
            webhook_enabled=webhook_enabled,
            on_backup_success=on_backup_success,
            on_backup_failure=on_backup_failure,
            on_storage_warning=on_storage_warning,
            on_node_offline=on_node_offline,
            on_rebalance=on_rebalance,
        ),
        _secrets_store(),
        "gk1",
    )


def _mock_http_client(status_code=200):
    """Return a mock httpx.AsyncClient that returns a response with given status."""
    response = MagicMock()
    response.status_code = status_code
    response.raise_for_status = MagicMock()
    if status_code >= 400:
        import httpx
        response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "error", request=MagicMock(), response=response
        )
    client = MagicMock()
    client.post = AsyncMock(return_value=response)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


# ── smtp.py ───────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_smtp_starttls_port_587():
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await smtp_mod.send_smtp(
            _smtp_config(port=587), "gk1", "error", "Disk full", None,
            password="pw",
        )
    _, kwargs = mock_send.call_args
    assert kwargs.get("start_tls") is True
    assert not kwargs.get("use_tls")


@pytest.mark.anyio
async def test_smtp_implicit_tls_port_465():
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await smtp_mod.send_smtp(
            _smtp_config(port=465), "gk1", "warning", "Node offline", "node-b",
            password="pw",
        )
    _, kwargs = mock_send.call_args
    assert kwargs.get("use_tls") is True


@pytest.mark.anyio
async def test_smtp_fetches_password_from_secrets():
    secrets = _secrets_store(smtp_password="fromstore")
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await smtp_mod.send_smtp(
            _smtp_config(), "gk1", "info", "test", None, secrets=secrets
        )
    _, kwargs = mock_send.call_args
    assert kwargs["password"] == "fromstore"


@pytest.mark.anyio
async def test_smtp_explicit_password_overrides_secrets():
    secrets = _secrets_store(smtp_password="fromstore")
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await smtp_mod.send_smtp(
            _smtp_config(), "gk1", "info", "test", None,
            secrets=secrets, password="explicit",
        )
    _, kwargs = mock_send.call_args
    assert kwargs["password"] == "explicit"


@pytest.mark.anyio
async def test_smtp_raises_without_password_or_secrets():
    with pytest.raises(ValueError, match="smtp"):
        await smtp_mod.send_smtp(_smtp_config(), "gk1", "info", "test", None)


@pytest.mark.anyio
async def test_smtp_subject_contains_level_and_message():
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await smtp_mod.send_smtp(
            _smtp_config(), "gk1", "critical", "Lifeboat corrupt", None, password="pw"
        )
    email_body: str = mock_send.call_args.args[0]
    assert "CRITICAL" in email_body
    assert "Lifeboat corrupt" in email_body


@pytest.mark.anyio
async def test_smtp_detail_included_in_body():
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        await smtp_mod.send_smtp(
            _smtp_config(), "gk1", "error", "msg", "extra detail line", password="pw"
        )
    email_body: str = mock_send.call_args.args[0]
    assert "extra detail line" in email_body


@pytest.mark.anyio
async def test_smtp_password_not_logged(caplog):
    import logging
    with patch("aiosmtplib.send", new_callable=AsyncMock):
        with caplog.at_level(logging.DEBUG, logger="gatekeeper.notify.smtp"):
            await smtp_mod.send_smtp(
                _smtp_config(), "gk1", "info", "test", None, password="supersecret"
            )
    for record in caplog.records:
        assert "supersecret" not in record.getMessage()


@pytest.mark.anyio
async def test_smtp_exception_propagates():
    import aiosmtplib
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = aiosmtplib.SMTPConnectError("refused")
        with pytest.raises(aiosmtplib.SMTPConnectError):
            await smtp_mod.send_smtp(
                _smtp_config(), "gk1", "error", "fail", None, password="pw"
            )


@pytest.mark.anyio
async def test_test_smtp_returns_true_on_success():
    with patch("aiosmtplib.send", new_callable=AsyncMock):
        result = await smtp_mod.test_smtp(_smtp_config(), password="pw")
    assert result is True


@pytest.mark.anyio
async def test_test_smtp_returns_false_on_failure():
    import aiosmtplib
    with patch("aiosmtplib.send", new_callable=AsyncMock) as mock_send:
        mock_send.side_effect = aiosmtplib.SMTPConnectError("refused")
        result = await smtp_mod.test_smtp(_smtp_config(), password="pw")
    assert result is False


# ── webhook.py ────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_webhook_posts_json_payload():
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        await webhook_mod.send_webhook(
            "gk1", "error", "Disk full", None, url="https://hook.example.com"
        )
    client.post.assert_awaited_once()
    args, kwargs = client.post.call_args
    assert args[0] == "https://hook.example.com"
    payload = kwargs["json"]
    assert payload["level"] == "error"
    assert payload["message"] == "Disk full"
    assert payload["node"] == "gk1"
    assert "timestamp" in payload


@pytest.mark.anyio
async def test_webhook_detail_included_when_provided():
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        await webhook_mod.send_webhook(
            "gk1", "warning", "msg", "some detail", url="https://hook.example.com"
        )
    payload = client.post.call_args.kwargs["json"]
    assert payload["detail"] == "some detail"


@pytest.mark.anyio
async def test_webhook_detail_absent_when_none():
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        await webhook_mod.send_webhook(
            "gk1", "info", "msg", None, url="https://hook.example.com"
        )
    payload = client.post.call_args.kwargs["json"]
    assert "detail" not in payload


@pytest.mark.anyio
async def test_webhook_fetches_url_from_secrets():
    secrets = _secrets_store(webhook_url="https://secret-hook.example.com")
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        await webhook_mod.send_webhook("gk1", "info", "msg", None, secrets=secrets)
    args, _ = client.post.call_args
    assert args[0] == "https://secret-hook.example.com"


@pytest.mark.anyio
async def test_webhook_explicit_url_overrides_secrets():
    secrets = _secrets_store(webhook_url="https://from-store.example.com")
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        await webhook_mod.send_webhook(
            "gk1", "info", "msg", None,
            secrets=secrets, url="https://explicit.example.com",
        )
    args, _ = client.post.call_args
    assert args[0] == "https://explicit.example.com"


@pytest.mark.anyio
async def test_webhook_raises_without_url_or_secrets():
    with pytest.raises(ValueError, match="webhook"):
        await webhook_mod.send_webhook("gk1", "info", "msg", None)


@pytest.mark.anyio
async def test_webhook_url_not_logged(caplog):
    import logging
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        with caplog.at_level(logging.DEBUG, logger="gatekeeper.notify.webhook"):
            await webhook_mod.send_webhook(
                "gk1", "info", "test", None, url="https://secret-url.example.com"
            )
    for record in caplog.records:
        assert "secret-url.example.com" not in record.getMessage()


@pytest.mark.anyio
async def test_webhook_http_status_error_propagates():
    import httpx
    client = _mock_http_client(status_code=500)
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.HTTPStatusError):
            await webhook_mod.send_webhook(
                "gk1", "error", "fail", None, url="https://hook.example.com"
            )


@pytest.mark.anyio
async def test_webhook_connect_error_propagates():
    import httpx
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client):
        with pytest.raises(httpx.ConnectError):
            await webhook_mod.send_webhook(
                "gk1", "error", "fail", None, url="https://hook.example.com"
            )


@pytest.mark.anyio
async def test_test_webhook_returns_true_on_success():
    client = _mock_http_client()
    with patch("httpx.AsyncClient", return_value=client):
        result = await webhook_mod.test_webhook(url="https://hook.example.com")
    assert result is True


@pytest.mark.anyio
async def test_test_webhook_returns_false_on_failure():
    import httpx
    client = MagicMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with patch("httpx.AsyncClient", return_value=client):
        result = await webhook_mod.test_webhook(url="https://hook.example.com")
    assert result is False


# ── dispatcher.py ─────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_dispatcher_sends_to_both_channels():
    d = _dispatcher()
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms, \
         patch.object(d, "_send_via_webhook", new_callable=AsyncMock) as mw:
        await d.send_alert("error", "Test")
    ms.assert_awaited_once()
    mw.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_smtp_only_when_webhook_disabled():
    d = _dispatcher(webhook_enabled=False)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms, \
         patch.object(d, "_send_via_webhook", new_callable=AsyncMock) as mw:
        await d.send_alert("error", "Test")
    ms.assert_awaited_once()
    mw.assert_not_awaited()


@pytest.mark.anyio
async def test_dispatcher_webhook_only_when_smtp_disabled():
    d = _dispatcher(smtp_enabled=False)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms, \
         patch.object(d, "_send_via_webhook", new_callable=AsyncMock) as mw:
        await d.send_alert("error", "Test")
    ms.assert_not_awaited()
    mw.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_no_channels_enabled():
    d = _dispatcher(smtp_enabled=False, webhook_enabled=False)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms, \
         patch.object(d, "_send_via_webhook", new_callable=AsyncMock) as mw:
        await d.send_alert("error", "Test")
    ms.assert_not_awaited()
    mw.assert_not_awaited()


@pytest.mark.anyio
async def test_dispatcher_event_filter_suppresses_disabled_event():
    d = _dispatcher(on_backup_success=False)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("info", "Backup OK", event="backup_success")
    ms.assert_not_awaited()


@pytest.mark.anyio
async def test_dispatcher_event_filter_allows_enabled_event():
    d = _dispatcher(on_backup_failure=True)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("error", "Backup failed", event="backup_failure")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_critical_bypasses_event_filter():
    d = _dispatcher(on_backup_success=False)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("critical", "Critical!", event="backup_success")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_none_event_always_dispatches():
    d = _dispatcher()
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("info", "No event key")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_unknown_event_key_dispatches_safely():
    d = _dispatcher()
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("warning", "Msg", event="unknown_event_xyz")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_smtp_failure_does_not_block_webhook():
    d = _dispatcher()
    with patch.object(d, "_send_via_smtp",
                      new_callable=AsyncMock, side_effect=Exception("smtp down")), \
         patch.object(d, "_send_via_webhook", new_callable=AsyncMock) as mw:
        await d.send_alert("error", "Test")  # must not raise
    mw.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_webhook_failure_does_not_block_smtp():
    d = _dispatcher()
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms, \
         patch.object(d, "_send_via_webhook",
                      new_callable=AsyncMock, side_effect=Exception("hook down")):
        await d.send_alert("error", "Test")  # must not raise
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_invalid_level_treated_as_error():
    d = _dispatcher()
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("BADLEVEL", "Test")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_storage_warning_event():
    d = _dispatcher(on_storage_warning=True)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("warning", "85% full", event="storage_warning")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_node_offline_event():
    d = _dispatcher(on_node_offline=True)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("warning", "node-b offline", event="node_offline")
    ms.assert_awaited_once()


@pytest.mark.anyio
async def test_dispatcher_rebalance_event():
    d = _dispatcher(on_rebalance=True)
    with patch.object(d, "_send_via_smtp", new_callable=AsyncMock) as ms:
        await d.send_alert("info", "Rebalance complete", event="rebalance")
    ms.assert_awaited_once()


def test_event_map_covers_all_notify_on_fields():
    """Verify _EVENT_TO_CONFIG covers every on_* field in NotifyConfig."""
    on_fields = {k for k in NotifyConfig.model_fields if k.startswith("on_")}
    mapped_attrs = set(_EVENT_TO_CONFIG.values())
    assert on_fields == mapped_attrs, (
        f"Event map missing: {on_fields - mapped_attrs}; "
        f"extra: {mapped_attrs - on_fields}"
    )
