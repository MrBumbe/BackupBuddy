"""Removal vote mechanism for cluster members.

Policy layer over ClusterDB — all SQL is handled by ClusterDB methods.

Vote lifecycle:
  propose_removal  → cast_vote (repeatedly) → start_grace_period → purge
  extend_grace_period → cast_vote (repeatedly) → apply_grace_extension

Cross-gatekeeper vote propagation is out of Phase 1 scope.  send_alert is
called locally to notify relevant nodes; network distribution is manual.
"""

import enum
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

VOTE_WINDOW_SECONDS: int = 48 * 3600
DEFAULT_GRACE_DAYS: int = 7


class VoteResult(str, enum.Enum):
    PENDING = "pending"
    PASSED  = "passed"
    FAILED  = "failed"
    EXPIRED = "expired"


@dataclass
class VoteRecord:
    vote_id:              int
    vote_type:            str
    target_node_id:       str
    proposed_by:          str
    proposed_at:          float
    closes_at:            float
    votes_yes:            int
    votes_no:             int
    resolved:             bool
    grace_extension_days: int | None


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _active_members(db) -> list[dict]:
    """Return members with status 'active' or 'grace'."""
    active = db.list_members(status="active")
    grace  = db.list_members(status="grace")
    return active + grace


def _eligible_voters(db, target_node_id: str) -> list[dict]:
    """Active/grace members excluding the removal target."""
    return [m for m in _active_members(db) if m["node_id"] != target_node_id]


def _majority_threshold(eligible_count: int) -> int:
    """Minimum yes votes required for a majority (strictly more than half)."""
    return eligible_count // 2 + 1


def _row_to_vote_record(row: dict) -> VoteRecord:
    return VoteRecord(
        vote_id=row["id"],
        vote_type=row["vote_type"],
        target_node_id=row["target_node_id"],
        proposed_by=row["proposed_by"],
        proposed_at=row["proposed_at"],
        closes_at=row["closes_at"],
        votes_yes=row["votes_yes"],
        votes_no=row["votes_no"],
        resolved=bool(row["resolved"]),
        grace_extension_days=row.get("grace_extension_days"),
    )


def _recount_and_resolve(db, vote_id: int, target_node_id: str) -> VoteResult:
    """Recount ballots, update aggregate columns, resolve if threshold crossed."""
    ballots = db.list_ballots(vote_id)
    yes_count = sum(1 for b in ballots if b["choice"] == 1)
    no_count  = sum(1 for b in ballots if b["choice"] == 0)

    db.update_vote(vote_id, votes_yes=yes_count, votes_no=no_count)

    eligible_count = len(_eligible_voters(db, target_node_id))
    threshold      = _majority_threshold(eligible_count)

    if yes_count >= threshold:
        db.update_vote(vote_id, resolved=1)
        return VoteResult.PASSED

    no_threshold = _majority_threshold(eligible_count)
    if no_count >= no_threshold:
        db.update_vote(vote_id, resolved=1)
        return VoteResult.FAILED

    return VoteResult.PENDING


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def propose_removal(
    db,
    target_node_id: str,
    proposed_by: str,
    *,
    send_alert=None,
) -> VoteRecord:
    """Open a removal vote for target_node_id.

    Raises ValueError if the proposer tries to remove themselves, if the
    target is not a current active/grace member, or if there is already an
    open removal vote for this target.
    """
    if target_node_id == proposed_by:
        raise ValueError("A node cannot propose its own removal")

    target = db.get_member(target_node_id)
    if target is None or target["status"] not in ("active", "grace"):
        raise ValueError(f"Target node {target_node_id!r} is not an active cluster member")

    open_votes = [
        v for v in db.list_votes(resolved=False)
        if v["vote_type"] == "removal" and v["target_node_id"] == target_node_id
    ]
    if open_votes:
        raise ValueError(f"A removal vote for {target_node_id!r} is already open")

    now      = time.time()
    closes   = now + VOTE_WINDOW_SECONDS
    vote_id  = db.insert_vote(
        vote_type="removal",
        target_node_id=target_node_id,
        proposed_by=proposed_by,
        proposed_at=now,
        closes_at=closes,
    )

    logger.info(
        "Removal vote %d opened: target=%s proposed_by=%s closes_at=%.0f",
        vote_id, target_node_id, proposed_by, closes,
    )

    if send_alert is not None:
        eligible = _eligible_voters(db, target_node_id)
        for member in eligible:
            send_alert(
                member["node_id"],
                f"Removal vote opened for {target['display_name']} (vote_id={vote_id})",
            )

    row = db.get_vote(vote_id)
    return _row_to_vote_record(row)


def cast_vote(
    db,
    vote_id: int,
    voter_node_id: str,
    choice: bool,
) -> VoteResult:
    """Record a yes/no ballot for an open vote.

    Returns the current VoteResult after recounting.  Raises ValueError if
    the vote does not exist, is already resolved or expired, the voter is not
    an eligible member, or the voter has already voted.
    """
    row = db.get_vote(vote_id)
    if row is None:
        raise ValueError(f"Vote {vote_id} not found")
    if row["resolved"]:
        raise ValueError(f"Vote {vote_id} is already resolved")
    if time.time() > row["closes_at"]:
        raise ValueError(f"Vote {vote_id} has expired")

    target_node_id = row["target_node_id"]

    if voter_node_id == target_node_id:
        raise ValueError("The vote target cannot cast a ballot in their own removal vote")

    voter = db.get_member(voter_node_id)
    if voter is None or voter["status"] in ("removed",):
        raise ValueError(f"Node {voter_node_id!r} is not an eligible voter")

    db.insert_ballot(
        vote_id=vote_id,
        voter_node_id=voter_node_id,
        voted_at=time.time(),
        choice=1 if choice else 0,
    )

    result = _recount_and_resolve(db, vote_id, target_node_id)
    logger.info(
        "Vote %d: %s cast %s — result=%s",
        vote_id, voter_node_id, "yes" if choice else "no", result.value,
    )
    return result


def check_vote_result(db, vote_id: int) -> VoteResult:
    """Return the current result without modifying any state.

    If the vote window has passed and it was never resolved, returns EXPIRED.
    If resolved=1 in the DB, returns PASSED when votes_yes > votes_no, else FAILED.
    """
    row = db.get_vote(vote_id)
    if row is None:
        raise ValueError(f"Vote {vote_id} not found")

    if row["resolved"]:
        return VoteResult.PASSED if row["votes_yes"] > row["votes_no"] else VoteResult.FAILED

    if time.time() > row["closes_at"]:
        return VoteResult.EXPIRED

    return VoteResult.PENDING


def start_grace_period(
    db,
    target_node_id: str,
    *,
    send_alert=None,
    trigger_refragmentation=None,
) -> None:
    """Transition a member from active to grace and begin their grace period.

    Raises ValueError if the member is not in 'active' status.
    """
    member = db.get_member(target_node_id)
    if member is None:
        raise ValueError(f"Member {target_node_id!r} not found")
    if member["status"] != "active":
        raise ValueError(
            f"Member {target_node_id!r} has status {member['status']!r}; "
            "expected 'active' to start grace period"
        )

    now = time.time()
    db.update_member(
        target_node_id,
        status="grace",
        grace_started_at=now,
    )

    logger.info(
        "Grace period started for %s at %.0f (grace_days=%d)",
        target_node_id, now, member.get("grace_days", DEFAULT_GRACE_DAYS),
    )

    if send_alert is not None:
        send_alert(
            target_node_id,
            "Your node has been voted for removal. "
            f"You have {member.get('grace_days', DEFAULT_GRACE_DAYS)} days "
            "to retrieve your data before your fragments are reallocated.",
        )

    if trigger_refragmentation is not None:
        trigger_refragmentation(target_node_id)


def extend_grace_period(
    db,
    target_node_id: str,
    days: int,
    proposed_by: str,
    *,
    send_alert=None,
) -> VoteRecord:
    """Open a vote to extend the grace period for target_node_id by `days` days.

    Raises ValueError if the target is not currently in grace status, or if
    there is already an open grace_extension vote for this target.
    """
    if days <= 0:
        raise ValueError("Grace extension days must be a positive integer")

    member = db.get_member(target_node_id)
    if member is None:
        raise ValueError(f"Member {target_node_id!r} not found")
    if member["status"] != "grace":
        raise ValueError(
            f"Member {target_node_id!r} is not in grace status; "
            "cannot extend grace period"
        )

    open_votes = [
        v for v in db.list_votes(resolved=False)
        if v["vote_type"] == "grace_extension" and v["target_node_id"] == target_node_id
    ]
    if open_votes:
        raise ValueError(
            f"A grace_extension vote for {target_node_id!r} is already open"
        )

    now     = time.time()
    closes  = now + VOTE_WINDOW_SECONDS
    vote_id = db.insert_vote(
        vote_type="grace_extension",
        target_node_id=target_node_id,
        proposed_by=proposed_by,
        proposed_at=now,
        closes_at=closes,
        grace_extension_days=days,
    )

    logger.info(
        "Grace extension vote %d opened: target=%s days=%d proposed_by=%s",
        vote_id, target_node_id, days, proposed_by,
    )

    if send_alert is not None:
        eligible = _eligible_voters(db, target_node_id)
        for member_rec in eligible:
            send_alert(
                member_rec["node_id"],
                f"Grace extension vote opened for {member['display_name']}: "
                f"+{days} days (vote_id={vote_id})",
            )

    row = db.get_vote(vote_id)
    return _row_to_vote_record(row)


def apply_grace_extension(db, vote_id: int) -> None:
    """Apply a passed grace_extension vote: add grace_extension_days to member's grace_days.

    Raises ValueError if the vote does not exist, is not a grace_extension vote,
    or has not passed.
    """
    row = db.get_vote(vote_id)
    if row is None:
        raise ValueError(f"Vote {vote_id} not found")
    if row["vote_type"] != "grace_extension":
        raise ValueError(f"Vote {vote_id} is not a grace_extension vote")
    if not row["resolved"] or row["votes_yes"] <= row["votes_no"]:
        raise ValueError(f"Vote {vote_id} has not passed")

    days = row["grace_extension_days"]
    if days is None or days <= 0:
        raise ValueError(f"Vote {vote_id} has invalid grace_extension_days: {days!r}")

    target_node_id = row["target_node_id"]
    member = db.get_member(target_node_id)
    if member is None:
        raise ValueError(f"Member {target_node_id!r} not found")

    new_grace_days = member["grace_days"] + days
    db.update_member(target_node_id, grace_days=new_grace_days)

    logger.info(
        "Grace extension applied: %s grace_days %d → %d (vote_id=%d)",
        target_node_id, member["grace_days"], new_grace_days, vote_id,
    )
