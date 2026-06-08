"""Unit tests for gatekeeper/cluster/join.py."""

import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gatekeeper.cluster.invites import generate_invite
from gatekeeper.cluster.join import (
    JoinAcceptResponse,
    JoinRequest,
    JoinResult,
    NodeInfo,
    accept_join,
    initiate_join,
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


@pytest.fixture
def invite(db, custom_wordlist):
    return generate_invite(db, "alice", wordlist_path=custom_wordlist)


@pytest.fixture
def node_info():
    return NodeInfo(
        node_id="node-abc-123",
        display_name="Bob's Home",
        tailscale_hostname="bobs-gatekeeper",
        profile="lagom",
    )


_FURL = "pb://aaaa@100.64.0.1:1234/introducer"


# ---------------------------------------------------------------------------
# NodeInfo validation
# ---------------------------------------------------------------------------

class TestNodeInfo:
    def test_valid(self, node_info):
        assert node_info.node_id == "node-abc-123"
        assert node_info.profile == "lagom"

    def test_empty_node_id_raises(self):
        with pytest.raises(Exception):
            NodeInfo(node_id="  ", display_name="x", tailscale_hostname="h")

    def test_empty_display_name_raises(self):
        with pytest.raises(Exception):
            NodeInfo(node_id="x", display_name="", tailscale_hostname="h")

    def test_invalid_profile_raises(self):
        with pytest.raises(Exception):
            NodeInfo(node_id="x", display_name="x", tailscale_hostname="h", profile="ultra")

    def test_all_valid_profiles(self):
        for p in ("lagom", "robust", "greedy", "adaptive"):
            ni = NodeInfo(node_id="x", display_name="x", tailscale_hostname="h", profile=p)
            assert ni.profile == p

    def test_whitespace_stripped(self):
        ni = NodeInfo(node_id="  x  ", display_name="  Bob  ", tailscale_hostname="  h  ")
        assert ni.node_id == "x"
        assert ni.display_name == "Bob"
        assert ni.tailscale_hostname == "h"


# ---------------------------------------------------------------------------
# accept_join — success
# ---------------------------------------------------------------------------

class TestAcceptJoinSuccess:
    def test_returns_join_accept_response(self, db, invite, node_info):
        result = accept_join(db, invite.code, node_info, _FURL)
        assert isinstance(result, JoinAcceptResponse)

    def test_introducer_furl_passed_through(self, db, invite, node_info):
        result = accept_join(db, invite.code, node_info, _FURL)
        assert result.introducer_furl == _FURL

    def test_new_node_appears_in_members(self, db, invite, node_info):
        result = accept_join(db, invite.code, node_info, _FURL)
        node_ids = [m["node_id"] for m in result.members]
        assert node_info.node_id in node_ids

    def test_invite_marked_used(self, db, invite, node_info):
        accept_join(db, invite.code, node_info, _FURL)
        row = db.get_invite(invite.code)
        assert row["used"] == 1

    def test_member_stored_in_db(self, db, invite, node_info):
        accept_join(db, invite.code, node_info, _FURL)
        member = db.get_member(node_info.node_id)
        assert member is not None
        assert member["display_name"] == node_info.display_name
        assert member["tailscale_hostname"] == node_info.tailscale_hostname
        assert member["profile"] == node_info.profile
        assert member["status"] == "active"

    def test_joined_at_is_recent(self, db, invite, node_info):
        before = time.time()
        accept_join(db, invite.code, node_info, _FURL)
        after = time.time()
        member = db.get_member(node_info.node_id)
        assert before <= member["joined_at"] <= after


# ---------------------------------------------------------------------------
# accept_join — failures
# ---------------------------------------------------------------------------

class TestAcceptJoinFailures:
    def test_unknown_invite_raises(self, db, node_info):
        with pytest.raises(ValueError):
            accept_join(db, "ghost-word-1", node_info, _FURL)

    def test_expired_invite_raises(self, db, invite, node_info):
        db._conn.execute(
            "UPDATE invites SET expires_at = ? WHERE code = ?",
            (time.time() - 1, invite.code),
        )
        db._conn.commit()
        with pytest.raises(ValueError):
            accept_join(db, invite.code, node_info, _FURL)

    def test_revoked_invite_raises(self, db, invite, node_info):
        db.update_invite(invite.code, revoked=1)
        with pytest.raises(ValueError):
            accept_join(db, invite.code, node_info, _FURL)

    def test_used_invite_raises(self, db, invite, node_info):
        db.update_invite(invite.code, used=1)
        with pytest.raises(ValueError):
            accept_join(db, invite.code, node_info, _FURL)

    def test_double_accept_raises(self, db, invite, node_info):
        accept_join(db, invite.code, node_info, _FURL)
        node2 = NodeInfo(
            node_id="different-node",
            display_name="Carol",
            tailscale_hostname="carol",
        )
        with pytest.raises(ValueError):
            accept_join(db, invite.code, node2, _FURL)


# ---------------------------------------------------------------------------
# accept_join — Fix (B): atomic insert + consume
# ---------------------------------------------------------------------------

class TestAcceptJoinAtomicity:
    def test_successful_join_marks_invite_used(self, db, invite, node_info):
        accept_join(db, invite.code, node_info, _FURL)
        row = db.get_invite(invite.code)
        assert row["used"] == 1


# ---------------------------------------------------------------------------
# accept_join — Fix (C): idempotent cascade retry
# ---------------------------------------------------------------------------

class TestAcceptJoinIdempotency:
    def test_retry_after_completed_join_returns_cluster_state(self, db, invite, node_info):
        """Cascade interrupted after leader committed: retry returns success."""
        accept_join(db, invite.code, node_info, _FURL)

        retry = accept_join(db, invite.code, node_info, _FURL)

        assert isinstance(retry, JoinAcceptResponse)
        assert retry.introducer_furl == _FURL
        assert any(m["node_id"] == node_info.node_id for m in retry.members)

    def test_used_invite_unknown_node_raises(self, db, invite, node_info):
        """Invite used but node not in members (split state) must raise, not silently succeed."""
        db.update_invite(invite.code, used=1)

        with pytest.raises(ValueError):
            accept_join(db, invite.code, node_info, _FURL)


# ---------------------------------------------------------------------------
# accept_join — Fix (D): fresh invite + node already a member
# ---------------------------------------------------------------------------

class TestAcceptJoinFreshInviteDuplicate:
    """Fresh (unused) invite code presented by a node that is already a member."""

    def _pre_insert(self, db, node_info):
        db.insert_member(
            node_id=node_info.node_id,
            display_name="Pre-existing node",
            tailscale_hostname="pre-existing",
            joined_at=time.time(),
        )

    def test_returns_success(self, db, invite, node_info):
        self._pre_insert(db, node_info)
        result = accept_join(db, invite.code, node_info, _FURL)
        assert isinstance(result, JoinAcceptResponse)
        assert result.introducer_furl == _FURL

    def test_invite_consumed(self, db, invite, node_info):
        self._pre_insert(db, node_info)
        accept_join(db, invite.code, node_info, _FURL)
        row = db.get_invite(invite.code)
        assert row["used"] == 1

    def test_no_duplicate_member_row(self, db, invite, node_info):
        self._pre_insert(db, node_info)
        members_before = len(db.list_members())
        accept_join(db, invite.code, node_info, _FURL)
        members_after = len(db.list_members())
        assert members_after == members_before
        # Existing row is untouched (not overwritten by node_info's display_name)
        stored = db.get_member(node_info.node_id)
        assert stored["display_name"] == "Pre-existing node"


# ---------------------------------------------------------------------------
# initiate_join — success
# ---------------------------------------------------------------------------

_VALID_RESPONSE = {
    "introducer_furl": _FURL,
    "members": [
        {
            "node_id": "existing-node",
            "display_name": "Alice",
            "tailscale_hostname": "alice-gk",
            "profile": "lagom",
        }
    ],
}


class TestInitiateJoinSuccess:
    @pytest.mark.anyio
    async def test_returns_join_result(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _VALID_RESPONSE

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("alpha-beta-1", node_info, "http://100.64.0.1:8080")

        assert isinstance(result, JoinResult)
        assert result.success is True

    @pytest.mark.anyio
    async def test_introducer_furl_returned(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _VALID_RESPONSE

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("alpha-beta-1", node_info, "http://100.64.0.1:8080")

        assert result.introducer_furl == _FURL

    @pytest.mark.anyio
    async def test_members_returned(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _VALID_RESPONSE

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("alpha-beta-1", node_info, "http://100.64.0.1:8080")

        assert len(result.members) == 1
        assert result.members[0]["node_id"] == "existing-node"

    @pytest.mark.anyio
    async def test_correct_url_called(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _VALID_RESPONSE

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            await initiate_join("alpha-beta-1", node_info, "http://100.64.0.1:8080/")

        called_url = mock_client.post.call_args[0][0]
        assert called_url == "http://100.64.0.1:8080/api/cluster/join"

    @pytest.mark.anyio
    async def test_request_body_contains_invite_code(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = _VALID_RESPONSE

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            await initiate_join("alpha-beta-3", node_info, "http://100.64.0.1:8080")

        kwargs = mock_client.post.call_args[1]
        assert kwargs["json"]["invite_code"] == "alpha-beta-3"


# ---------------------------------------------------------------------------
# initiate_join — failures
# ---------------------------------------------------------------------------

class TestInitiateJoinFailures:
    @pytest.mark.anyio
    async def test_http_400_returns_failure(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.json.return_value = {"error": "Invalid invite"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("bad-code-1", node_info, "http://100.64.0.1:8080")

        assert result.success is False
        assert "400" in result.error

    @pytest.mark.anyio
    async def test_network_error_returns_failure(self, node_info):
        import httpx as _httpx

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(
            side_effect=_httpx.ConnectError("Connection refused")
        )
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("alpha-beta-1", node_info, "http://10.0.0.1:8080")

        assert result.success is False
        assert result.error != ""

    @pytest.mark.anyio
    async def test_malformed_response_returns_failure(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"unexpected": "garbage"}

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("alpha-beta-1", node_info, "http://100.64.0.1:8080")

        assert result.success is False
        assert "Invalid response" in result.error

    @pytest.mark.anyio
    async def test_empty_furl_in_response_returns_failure(self, node_info):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "introducer_furl": "",
            "members": [],
        }

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("gatekeeper.cluster.join.httpx.AsyncClient", return_value=mock_client):
            result = await initiate_join("alpha-beta-1", node_info, "http://100.64.0.1:8080")

        assert result.success is False
