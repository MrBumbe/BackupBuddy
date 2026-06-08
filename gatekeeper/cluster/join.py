"""Cluster join flow.

Two-sided protocol:
- accept_join: called on the existing gatekeeper when a new node presents an invite.
- initiate_join: called on the joining gatekeeper to contact an existing member.

All inbound HTTP data is validated with Pydantic before touching the database
(SECURITY.md §4 — all cluster messages are untrusted).
"""

import logging
import time
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel, field_validator

from gatekeeper.cluster.invites import validate_invite

logger = logging.getLogger(__name__)

_VALID_PROFILES = frozenset({"lagom", "robust", "greedy", "adaptive"})


# ── Shared models ─────────────────────────────────────────────────────────────

class NodeInfo(BaseModel):
    """Identifying information about a gatekeeper node joining the cluster."""

    node_id: str
    display_name: str
    tailscale_hostname: str
    profile: str = "lagom"

    @field_validator("node_id", "display_name", "tailscale_hostname")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field must not be empty")
        return v.strip()

    @field_validator("profile")
    @classmethod
    def _valid_profile(cls, v: str) -> str:
        if v not in _VALID_PROFILES:
            raise ValueError(
                f"Unknown profile {v!r} — must be one of {sorted(_VALID_PROFILES)}"
            )
        return v


# ── HTTP protocol models ──────────────────────────────────────────────────────

class JoinRequest(BaseModel):
    """Body sent to POST /api/cluster/join on an existing gatekeeper."""

    invite_code: str
    node_info: NodeInfo


class _MemberEntry(BaseModel):
    """A single cluster member as returned in the join response."""

    node_id: str
    display_name: str
    tailscale_hostname: str
    profile: str


class _JoinResponseBody(BaseModel):
    """Validates the HTTP response from an existing gatekeeper.

    Treated as untrusted cluster data — must pass Pydantic validation before
    any field is used or stored locally (SECURITY.md §4).
    """

    introducer_furl: str
    members: list[_MemberEntry]

    @field_validator("introducer_furl")
    @classmethod
    def _furl_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("introducer_furl must not be empty")
        return v


# ── Return types ──────────────────────────────────────────────────────────────

@dataclass
class JoinAcceptResponse:
    """Returned by accept_join; serialised to JSON by the route handler."""

    introducer_furl: str
    members: list[dict]


@dataclass
class JoinResult:
    """Returned by initiate_join to the onboarding wizard.

    The caller is responsible for persisting introducer_furl to the local
    Tahoe client config and for inserting self into the local cluster.db.
    """

    success: bool
    introducer_furl: str = ""
    members: list[dict] = field(default_factory=list)
    error: str = ""


# ── Server-side: accept an incoming join request ──────────────────────────────

def accept_join(
    db,
    invite_code: str,
    node_info: NodeInfo,
    introducer_furl: str,
) -> JoinAcceptResponse:
    """Validate and consume the invite, register the new member, return cluster state.

    Idempotent for cascade retries: if the invite is already used AND this
    node_id is already a member, the join completed on the leader side before
    the joiner crashed — return cluster state so the cascade can continue.

    Raises ValueError if the code is invalid, expired, revoked, or used by a
    different node; or if node_id conflicts with an existing member record.
    """
    # Fix (C): idempotent retry — invite is used but this node is already a member.
    # This handles a cascade interrupted after the leader committed but before the
    # joiner persisted root_dir.cap locally.  On retry the joiner sends the same
    # invite code; we return success instead of 400.
    raw_invite = db.get_invite(invite_code)
    if raw_invite is not None and raw_invite["used"]:
        existing_member = db.get_member(node_info.node_id)
        if existing_member is not None:
            members = db.list_members(status="active")
            logger.info(
                "Node '%s' (%s) retried join — already a member, returning cluster state",
                node_info.display_name,
                node_info.node_id,
            )
            return JoinAcceptResponse(introducer_furl=introducer_furl, members=members)

    # Validate the invite.  Returns None if invalid, expired, revoked, or used
    # by a node that is NOT yet in the members table (genuine split state).
    if validate_invite(db, invite_code) is None:
        raise ValueError("Invalid, expired, or already-used invite code")

    # Fix (D): fresh invite + node already a member.
    # Handles the scenario where an operator generates a new invite for a node
    # that already completed its join (e.g. after a cascade failure followed by
    # a fresh code). Consume the invite and return success without inserting a
    # duplicate member row.
    if db.get_member(node_info.node_id) is not None:
        db.update_invite(invite_code, used=1)
        members = db.list_members(status="active")
        logger.info(
            "Node '%s' (%s) presented a fresh invite but is already a member"
            " — invite consumed, returning cluster state",
            node_info.display_name,
            node_info.node_id,
        )
        return JoinAcceptResponse(introducer_furl=introducer_furl, members=members)

    # Fix (B): insert member and mark invite used in one atomic transaction.
    # If insert raises IntegrityError (duplicate node_id) the invite is NOT
    # consumed — the admin does not need to generate a new code.
    db.accept_new_member(
        node_id=node_info.node_id,
        display_name=node_info.display_name,
        tailscale_hostname=node_info.tailscale_hostname,
        joined_at=time.time(),
        profile=node_info.profile,
        invite_code=invite_code,
    )

    members = db.list_members(status="active")
    logger.info(
        "Node '%s' (%s) joined the cluster",
        node_info.display_name,
        node_info.node_id,
    )
    return JoinAcceptResponse(introducer_furl=introducer_furl, members=members)


# ── Client-side: initiate a join request ─────────────────────────────────────

async def initiate_join(
    invite_code: str,
    node_info: NodeInfo,
    member_url: str,
) -> JoinResult:
    """POST join request to an existing cluster member and return the result.

    The HTTP response is validated with Pydantic before any field is returned.
    The caller (onboarding wizard) is responsible for persisting the
    introducer_furl and configuring the local Tahoe client.

    member_url must be a Tailscale-reachable base URL of an existing gatekeeper
    (e.g. "http://100.x.y.z:8080") — the joining node must have joined the
    Tailscale network before calling this function.
    """
    url = f"{member_url.rstrip('/')}/api/cluster/join"
    body = JoinRequest(invite_code=invite_code, node_info=node_info)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=body.model_dump())
    except httpx.RequestError as exc:
        logger.error("Cluster join request to %s failed: %s", member_url, exc)
        return JoinResult(success=False, error=f"Network error: {exc}")

    if response.status_code != 200:
        logger.warning(
            "Cluster join rejected by %s: HTTP %d",
            member_url,
            response.status_code,
        )
        return JoinResult(
            success=False,
            error=f"Join rejected: HTTP {response.status_code}",
        )

    try:
        data = _JoinResponseBody.model_validate(response.json())
    except Exception as exc:
        logger.error("Invalid join response from %s: %s", member_url, exc)
        return JoinResult(success=False, error=f"Invalid response from peer: {exc}")

    logger.info("Successfully joined cluster via %s", member_url)
    return JoinResult(
        success=True,
        introducer_furl=data.introducer_furl,
        members=[m.model_dump() for m in data.members],
    )
