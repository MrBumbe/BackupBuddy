"""
Unit tests for agent/gatekeeper_client.py.

Covers:
  - register(): success, HTTP error → RegistrationError, connection error → RegistrationError
  - send_fragment(): success, HTTP error → IOError
  - store_lifeboat(): writes to disk, sets 0600 permissions
  - get_lifeboat(): reads from disk, raises FileNotFoundError when missing
"""

from __future__ import annotations

import asyncio
import stat
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.gatekeeper_client import GatekeeperClient, RegistrationError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def lifeboat_path(tmp_path: Path) -> Path:
    return tmp_path / "lifeboat.enc"


@pytest.fixture
def client(lifeboat_path: Path) -> GatekeeperClient:
    return GatekeeperClient(
        url="http://192.168.1.50:8081",
        token="test-token",
        agent_name="test-laptop",
        lifeboat_path=lifeboat_path,
    )


# ── register() ────────────────────────────────────────────────────────────────

class TestRegister:
    def test_success_calls_correct_endpoint(self, client: GatekeeperClient) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(client._client, "post", mock_post):
            asyncio.run(client.register())

        mock_post.assert_awaited_once()
        call_url = mock_post.call_args[0][0]
        assert call_url == "http://192.168.1.50:8081/api/agents/register"

    def test_success_sends_agent_name(self, client: GatekeeperClient) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(client._client, "post", mock_post):
            asyncio.run(client.register())

        payload = mock_post.call_args[1]["json"]
        assert payload == {"agent_name": "test-laptop"}

    def test_http_401_raises_registration_error(self, client: GatekeeperClient) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "401", request=MagicMock(), response=MagicMock(status_code=401)
        )
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(client._client, "post", mock_post):
            with pytest.raises(RegistrationError, match="401"):
                asyncio.run(client.register())

    def test_http_403_raises_registration_error(self, client: GatekeeperClient) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "403", request=MagicMock(), response=MagicMock(status_code=403)
        )
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(client._client, "post", mock_post):
            with pytest.raises(RegistrationError, match="403"):
                asyncio.run(client.register())

    def test_connection_error_raises_registration_error(self, client: GatekeeperClient) -> None:
        mock_post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

        with patch.object(client._client, "post", mock_post):
            with pytest.raises(RegistrationError, match="192.168.1.50"):
                asyncio.run(client.register())

    def test_bearer_token_sent_in_header(self, client: GatekeeperClient) -> None:
        assert client._client.headers["authorization"] == "Bearer test-token"


# ── send_fragment() ───────────────────────────────────────────────────────────

class TestSendFragment:
    def test_success_posts_to_fragments_endpoint(self, client: GatekeeperClient) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(client._client, "post", mock_post):
            asyncio.run(client.send_fragment(b"fragment-data", {"file_id": "abc123"}))

        call_url = mock_post.call_args[0][0]
        assert call_url == "http://192.168.1.50:8081/api/agents/fragments"

    def test_http_error_raises_ioerror(self, client: GatekeeperClient) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        mock_post = AsyncMock(return_value=mock_resp)

        with patch.object(client._client, "post", mock_post):
            with pytest.raises(IOError):
                asyncio.run(client.send_fragment(b"data", {}))


# ── store_lifeboat() ──────────────────────────────────────────────────────────

class TestStoreLifeboat:
    def test_writes_bundle_to_disk(self, client: GatekeeperClient, lifeboat_path: Path) -> None:
        bundle = b"encrypted-lifeboat-bundle"
        client.store_lifeboat(bundle)
        assert lifeboat_path.read_bytes() == bundle

    def test_overwrites_existing_bundle(self, client: GatekeeperClient, lifeboat_path: Path) -> None:
        client.store_lifeboat(b"old-bundle")
        client.store_lifeboat(b"new-bundle")
        assert lifeboat_path.read_bytes() == b"new-bundle"

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested_path = tmp_path / "nested" / "dir" / "lifeboat.enc"
        c = GatekeeperClient("http://x", "tok", "agent", lifeboat_path=nested_path)
        c.store_lifeboat(b"data")
        assert nested_path.exists()

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod not enforced on Windows")
    def test_sets_0600_permissions(self, client: GatekeeperClient, lifeboat_path: Path) -> None:
        client.store_lifeboat(b"secret-bundle")
        mode = stat.S_IMODE(lifeboat_path.stat().st_mode)
        assert mode == 0o600


# ── get_lifeboat() ────────────────────────────────────────────────────────────

class TestGetLifeboat:
    def test_returns_stored_bundle(self, client: GatekeeperClient) -> None:
        bundle = b"my-lifeboat-bundle"
        client.store_lifeboat(bundle)
        assert client.get_lifeboat() == bundle

    def test_raises_file_not_found_when_no_bundle(
        self, client: GatekeeperClient, lifeboat_path: Path
    ) -> None:
        assert not lifeboat_path.exists()
        with pytest.raises(FileNotFoundError):
            client.get_lifeboat()
