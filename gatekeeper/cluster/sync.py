"""Cross-gatekeeper vote and ballot synchronisation (ADR-021).

Phase 1 protocol:
  - Proposer pushes the vote record to all active peers after creation.
  - Non-proposer forwards a ballot to the proposer, which stores it and
    returns the updated vote result.

Phase 1 assumptions (see ADR-021):
  - All gatekeeper nodes use the same web port.
  - Push is fire-and-forget; no retry on failure.
  - Voter identity is always derived server-side from the sender's Tailscale IP.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── Pydantic sync models (all inbound cluster data must be validated) ──────────

class VoteSyncMessage(BaseModel):
    """Vote record pushed from the proposer to all peers."""

    vote_id: int
    vote_type: str
    target_node_id: str
    proposed_by: str
    proposed_at: float
    closes_at: float
    votes_yes: int = 0
    votes_no: int = 0
    resolved: bool = False
    grace_extension_days: int | None = None


class BallotSyncMessage(BaseModel):
    """Ballot forwarded from a voter to the proposer node.

    voter_node_id is intentionally absent — the proposer derives the voter's
    identity from the sender's Tailscale IP (ADR-021 security requirement).
    """

    vote_id: int
    voted_at: float
    choice: bool


# ── Outbound push helpers ─────────────────────────────────────────────────────

async def push_vote_to_peers(
    vote_row: dict[str, Any],
    members: list[dict[str, Any]],
    local_node_id: str,
    web_port: int,
) -> None:
    """Push a newly created vote to all active cluster peers.

    Fire-and-forget per ADR-021: logs failures but does not raise.
    """
    msg = VoteSyncMessage(
        vote_id=vote_row["id"],
        vote_type=vote_row["vote_type"],
        target_node_id=vote_row["target_node_id"],
        proposed_by=vote_row["proposed_by"],
        proposed_at=vote_row["proposed_at"],
        closes_at=vote_row["closes_at"],
        votes_yes=vote_row.get("votes_yes", 0),
        votes_no=vote_row.get("votes_no", 0),
        resolved=bool(vote_row.get("resolved", False)),
        grace_extension_days=vote_row.get("grace_extension_days"),
    )

    async with httpx.AsyncClient(timeout=10.0) as client:
        for member in members:
            if member["node_id"] == local_node_id:
                continue
            if member.get("status") not in ("active", "grace"):
                continue
            # ADR-010: removal target must not know a vote is open against them
            if msg.vote_type == "removal" and member["node_id"] == msg.target_node_id:
                continue
            hostname = member.get("tailscale_hostname", "")
            if not hostname:
                continue
            url = f"http://{hostname}:{web_port}/api/cluster/sync/vote"
            try:
                resp = await client.post(url, json=msg.model_dump())
                if resp.status_code != 200:
                    logger.warning(
                        "sync/vote to %s returned HTTP %d", hostname, resp.status_code
                    )
                else:
                    logger.debug("sync/vote pushed to %s (vote_id=%d)", hostname, msg.vote_id)
            except httpx.RequestError as exc:
                logger.warning("sync/vote to %s failed: %s", hostname, exc)


async def push_ballot_to_proposer(
    ballot_msg: BallotSyncMessage,
    proposer_hostname: str,
    web_port: int,
) -> dict[str, Any]:
    """Forward a ballot to the proposer node.

    Returns the proposer's JSON response on success.
    Raises ValueError on network failure or HTTP error.
    """
    url = f"http://{proposer_hostname}:{web_port}/api/cluster/sync/ballot"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=ballot_msg.model_dump())
    except httpx.RequestError as exc:
        raise ValueError(f"Network error reaching proposer: {exc}") from exc
    if resp.status_code != 200:
        raise ValueError(
            f"Proposer rejected ballot: HTTP {resp.status_code} — {resp.text[:200]}"
        )
    return resp.json()
