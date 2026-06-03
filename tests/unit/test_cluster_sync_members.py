"""Unit tests for member list sync (push and fetch) in gatekeeper/cluster/sync.py."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatekeeper.cluster.invites import generate_invite
from gatekeeper.cluster.join import NodeInfo, accept_join
from gatekeeper.cluster.sync import (
    MemberEntry,
    MemberListPushMessage,
    fetch_member_list_from_peer,
    push_member_list_to_peers,
)
from gatekeeper.db.cluster import ClusterDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with ClusterDB(str(tmp_path / "cluster.db")) as database:
        yield database


@pytest.fixture
def custom_wordlist(tmp_path):
    path = tmp_path / "wordlist.txt"
    path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    return path


def _make_member(node_id: str, hostname: str, status: str = "active") -> dict:
    return {
        "node_id": node_id,
        "display_name": f"Node {node_id}",
        "tailscale_hostname": hostname,
        "profile": "lagom",
        "status": status,
    }


# ---------------------------------------------------------------------------
# MemberEntry / MemberListPushMessage models
# ---------------------------------------------------------------------------

class TestMemberEntry:
    def test_valid(self):
        entry = MemberEntry(
            node_id="alice",
            display_name="Alice",
            tailscale_hostname="alice-gk",
            profile="lagom",
        )
        assert entry.node_id == "alice"

    def test_push_message_empty_members(self):
        msg = MemberListPushMessage(members=[])
        assert msg.members == []

    def test_push_message_round_trip(self):
        msg = MemberListPushMessage(members=[
            MemberEntry(node_id="a", display_name="A", tailscale_hostname="h-a", profile="lagom"),
            MemberEntry(node_id="b", display_name="B", tailscale_hostname="h-b", profile="robust"),
        ])
        dumped = msg.model_dump()
        restored = MemberListPushMessage.model_validate(dumped)
        assert len(restored.members) == 2
        assert restored.members[0].node_id == "a"


# ---------------------------------------------------------------------------
# push_member_list_to_peers — targeting logic
# ---------------------------------------------------------------------------

class TestPushMemberListToPeers:
    @pytest.mark.anyio
    async def test_sends_to_active_peers_not_self(self):
        members = [
            _make_member("self", "self-gk"),
            _make_member("alice", "alice-gk"),
            _make_member("bob", "bob-gk"),
        ]
        posted_urls = []

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=lambda url, **_: posted_urls.append(url) or mock_resp
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            await push_member_list_to_peers(members, local_node_id="self", web_port=8080)

        assert len(posted_urls) == 2
        assert any("alice-gk" in u for u in posted_urls)
        assert any("bob-gk" in u for u in posted_urls)
        assert not any("self-gk" in u for u in posted_urls)

    @pytest.mark.anyio
    async def test_excludes_new_joiner(self):
        members = [
            _make_member("self", "self-gk"),
            _make_member("alice", "alice-gk"),
            _make_member("newnode", "newnode-gk"),
        ]
        posted_urls = []

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=lambda url, **_: posted_urls.append(url) or mock_resp
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            await push_member_list_to_peers(
                members,
                local_node_id="self",
                web_port=8080,
                exclude_node_id="newnode",
            )

        assert len(posted_urls) == 1
        assert "alice-gk" in posted_urls[0]
        assert not any("newnode-gk" in u for u in posted_urls)

    @pytest.mark.anyio
    async def test_skips_non_active_members(self):
        members = [
            _make_member("self", "self-gk"),
            _make_member("alice", "alice-gk"),
            _make_member("removed", "removed-gk", status="removed"),
        ]
        posted_urls = []

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=lambda url, **_: posted_urls.append(url) or mock_resp
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            await push_member_list_to_peers(members, local_node_id="self", web_port=8080)

        assert len(posted_urls) == 1
        assert "alice-gk" in posted_urls[0]

    @pytest.mark.anyio
    async def test_payload_includes_all_active_members(self):
        members = [
            _make_member("self", "self-gk"),
            _make_member("alice", "alice-gk"),
            _make_member("bob", "bob-gk"),
        ]
        payloads = []

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=lambda url, json=None, **_: payloads.append(json) or mock_resp
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            await push_member_list_to_peers(members, local_node_id="self", web_port=8080)

        assert len(payloads) > 0
        node_ids = {m["node_id"] for m in payloads[0]["members"]}
        assert "self" in node_ids
        assert "alice" in node_ids
        assert "bob" in node_ids

    @pytest.mark.anyio
    async def test_no_error_on_network_failure(self):
        import httpx as _httpx

        members = [
            _make_member("self", "self-gk"),
            _make_member("alice", "alice-gk"),
        ]
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=_httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            # must not raise
            await push_member_list_to_peers(members, local_node_id="self", web_port=8080)


# ---------------------------------------------------------------------------
# fetch_member_list_from_peer
# ---------------------------------------------------------------------------

class TestFetchMemberListFromPeer:
    @pytest.mark.anyio
    async def test_returns_members_on_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "members": [
                {"node_id": "alice", "display_name": "Alice",
                 "tailscale_hostname": "alice-gk", "profile": "lagom"},
            ]
        }
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_member_list_from_peer("alice-gk", 8080)

        assert result is not None
        assert len(result) == 1
        assert result[0]["node_id"] == "alice"

    @pytest.mark.anyio
    async def test_returns_none_on_http_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 503
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_member_list_from_peer("alice-gk", 8080)

        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_on_network_error(self):
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=_httpx.ConnectError("refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_member_list_from_peer("unreachable-gk", 8080)

        assert result is None

    @pytest.mark.anyio
    async def test_returns_none_on_invalid_json(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"unexpected": "garbage"}
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.sync.httpx.AsyncClient", return_value=mock_client):
            result = await fetch_member_list_from_peer("alice-gk", 8080)

        assert result is None


# ---------------------------------------------------------------------------
# upsert_peer_member — does not clobber status/grace/joined_at
# ---------------------------------------------------------------------------

class TestUpsertPeerMemberSafety:
    def test_upsert_does_not_overwrite_status(self, db):
        db.insert_member(
            node_id="alice",
            display_name="Alice",
            tailscale_hostname="alice-gk",
            joined_at=time.time() - 100,
            profile="lagom",
            status="grace",
        )
        db.upsert_peer_member(
            node_id="alice",
            display_name="Alice Updated",
            tailscale_hostname="alice-gk-new",
            profile="robust",
        )
        row = db.get_member("alice")
        assert row["status"] == "grace"
        assert row["display_name"] == "Alice Updated"
        assert row["tailscale_hostname"] == "alice-gk-new"
        assert row["profile"] == "robust"

    def test_upsert_does_not_overwrite_joined_at(self, db):
        joined = time.time() - 999
        db.insert_member(
            node_id="bob",
            display_name="Bob",
            tailscale_hostname="bob-gk",
            joined_at=joined,
            profile="lagom",
            status="active",
        )
        db.upsert_peer_member(
            node_id="bob",
            display_name="Bob Updated",
            tailscale_hostname="bob-gk",
            profile="lagom",
        )
        row = db.get_member("bob")
        assert row["joined_at"] == joined

    def test_upsert_inserts_new_member(self, db):
        db.upsert_peer_member(
            node_id="carol",
            display_name="Carol",
            tailscale_hostname="carol-gk",
            profile="lagom",
        )
        row = db.get_member("carol")
        assert row is not None
        assert row["node_id"] == "carol"
        assert row["status"] == "active"
