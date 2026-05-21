"""Unit tests for gatekeeper/cluster/removal.py."""

import time

import pytest

from gatekeeper.db.cluster import ClusterDB
from gatekeeper.cluster.removal import (
    DEFAULT_GRACE_DAYS,
    VOTE_WINDOW_SECONDS,
    VoteRecord,
    VoteResult,
    apply_grace_extension,
    cast_vote,
    check_vote_result,
    extend_grace_period,
    propose_removal,
    start_grace_period,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with ClusterDB(str(tmp_path / "cluster.db")) as database:
        yield database


def _add_member(db, node_id: str, display_name: str = "Test", status: str = "active"):
    db.insert_member(
        node_id=node_id,
        display_name=display_name,
        tailscale_hostname=f"{node_id}.ts.net",
        joined_at=time.time(),
        status=status,
    )


# ---------------------------------------------------------------------------
# propose_removal
# ---------------------------------------------------------------------------

class TestProposeRemoval:
    def test_returns_vote_record(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, target_node_id="bob", proposed_by="alice")
        assert isinstance(vote, VoteRecord)
        assert vote.vote_type == "removal"
        assert vote.target_node_id == "bob"
        assert vote.proposed_by == "alice"
        assert not vote.resolved

    def test_vote_stored_in_db(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, "bob", "alice")
        row = db.get_vote(vote.vote_id)
        assert row is not None
        assert row["vote_type"] == "removal"
        assert row["target_node_id"] == "bob"

    def test_closes_at_48h(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        before = time.time()
        vote = propose_removal(db, "bob", "alice")
        after = time.time()
        assert before + VOTE_WINDOW_SECONDS <= vote.closes_at <= after + VOTE_WINDOW_SECONDS

    def test_self_removal_raises(self, db):
        _add_member(db, "alice")
        with pytest.raises(ValueError, match="cannot propose its own removal"):
            propose_removal(db, "alice", "alice")

    def test_nonexistent_target_raises(self, db):
        _add_member(db, "alice")
        with pytest.raises(ValueError, match="not an active cluster member"):
            propose_removal(db, "ghost", "alice")

    def test_removed_target_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob", status="removed")
        with pytest.raises(ValueError, match="not an active cluster member"):
            propose_removal(db, "bob", "alice")

    def test_duplicate_open_vote_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        propose_removal(db, "bob", "alice")
        with pytest.raises(ValueError, match="already open"):
            propose_removal(db, "bob", "alice")

    def test_send_alert_called_for_eligible_members(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        alerts = []
        propose_removal(db, "bob", "alice", send_alert=lambda nid, msg: alerts.append(nid))
        assert "alice" in alerts
        assert "carol" in alerts
        assert "bob" not in alerts

    def test_no_alert_called_when_none(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        propose_removal(db, "bob", "alice", send_alert=None)


# ---------------------------------------------------------------------------
# cast_vote
# ---------------------------------------------------------------------------

class TestCastVote:
    def test_yes_vote_increments_count(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "carol", True)
        row = db.get_vote(vote.vote_id)
        assert row["votes_yes"] == 1
        assert row["votes_no"] == 0

    def test_no_vote_increments_count(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "carol", False)
        row = db.get_vote(vote.vote_id)
        assert row["votes_no"] == 1
        assert row["votes_yes"] == 0

    def test_double_vote_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "carol", True)
        with pytest.raises(ValueError, match="already voted"):
            cast_vote(db, vote.vote_id, "carol", False)

    def test_target_cannot_vote(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, "bob", "alice")
        with pytest.raises(ValueError, match="cannot cast a ballot"):
            cast_vote(db, vote.vote_id, "bob", True)

    def test_removed_member_cannot_vote(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol", status="removed")
        vote = propose_removal(db, "bob", "alice")
        with pytest.raises(ValueError, match="not an eligible voter"):
            cast_vote(db, vote.vote_id, "carol", True)

    def test_nonexistent_voter_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, "bob", "alice")
        with pytest.raises(ValueError, match="not an eligible voter"):
            cast_vote(db, vote.vote_id, "ghost", True)

    def test_nonexistent_vote_raises(self, db):
        _add_member(db, "alice")
        with pytest.raises(ValueError, match="not found"):
            cast_vote(db, 9999, "alice", True)

    def test_expired_vote_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        db._conn.execute(
            "UPDATE votes SET closes_at = ? WHERE id = ?",
            (time.time() - 1, vote.vote_id),
        )
        db._conn.commit()
        with pytest.raises(ValueError, match="expired"):
            cast_vote(db, vote.vote_id, "carol", True)

    def test_majority_passes_vote(self, db):
        # 3 members, target=bob, eligible=alice+carol → majority=2
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "alice", True)
        result = cast_vote(db, vote.vote_id, "carol", True)
        assert result == VoteResult.PASSED
        row = db.get_vote(vote.vote_id)
        assert row["resolved"] == 1

    def test_majority_no_fails_vote(self, db):
        # 3 members, target=bob, eligible=alice+carol → majority=2 no votes
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "alice", False)
        result = cast_vote(db, vote.vote_id, "carol", False)
        assert result == VoteResult.FAILED

    def test_pending_while_below_threshold(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        _add_member(db, "dave")
        # eligible=alice+carol+dave → majority=2
        vote = propose_removal(db, "bob", "alice")
        result = cast_vote(db, vote.vote_id, "alice", True)
        assert result == VoteResult.PENDING

    def test_vote_on_resolved_vote_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "alice", True)
        cast_vote(db, vote.vote_id, "carol", True)
        with pytest.raises(ValueError, match="already resolved"):
            cast_vote(db, vote.vote_id, "carol", True)


# ---------------------------------------------------------------------------
# check_vote_result
# ---------------------------------------------------------------------------

class TestCheckVoteResult:
    def test_pending_open_vote(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, "bob", "alice")
        assert check_vote_result(db, vote.vote_id) == VoteResult.PENDING

    def test_passed_resolved_vote(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "alice", True)
        cast_vote(db, vote.vote_id, "carol", True)
        assert check_vote_result(db, vote.vote_id) == VoteResult.PASSED

    def test_expired_unresolved_vote(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, "bob", "alice")
        db._conn.execute(
            "UPDATE votes SET closes_at = ? WHERE id = ?",
            (time.time() - 1, vote.vote_id),
        )
        db._conn.commit()
        assert check_vote_result(db, vote.vote_id) == VoteResult.EXPIRED

    def test_nonexistent_vote_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            check_vote_result(db, 9999)

    def test_does_not_modify_db(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        vote = propose_removal(db, "bob", "alice")
        before = db.get_vote(vote.vote_id)
        check_vote_result(db, vote.vote_id)
        after = db.get_vote(vote.vote_id)
        assert dict(before) == dict(after)


# ---------------------------------------------------------------------------
# start_grace_period
# ---------------------------------------------------------------------------

class TestStartGracePeriod:
    def test_sets_status_to_grace(self, db):
        _add_member(db, "bob")
        start_grace_period(db, "bob")
        member = db.get_member("bob")
        assert member["status"] == "grace"

    def test_sets_grace_started_at(self, db):
        before = time.time()
        _add_member(db, "bob")
        start_grace_period(db, "bob")
        after = time.time()
        member = db.get_member("bob")
        assert before <= member["grace_started_at"] <= after

    def test_nonexistent_member_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            start_grace_period(db, "ghost")

    def test_already_in_grace_raises(self, db):
        _add_member(db, "bob", status="grace")
        with pytest.raises(ValueError, match="expected 'active'"):
            start_grace_period(db, "bob")

    def test_send_alert_called_for_target(self, db):
        _add_member(db, "bob")
        alerts = []
        start_grace_period(db, "bob", send_alert=lambda nid, msg: alerts.append(nid))
        assert "bob" in alerts

    def test_trigger_refragmentation_called(self, db):
        _add_member(db, "bob")
        called = []
        start_grace_period(db, "bob", trigger_refragmentation=lambda nid: called.append(nid))
        assert called == ["bob"]


# ---------------------------------------------------------------------------
# extend_grace_period
# ---------------------------------------------------------------------------

class TestExtendGracePeriod:
    def test_returns_vote_record(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob", status="grace")
        vote = extend_grace_period(db, "bob", days=7, proposed_by="alice")
        assert isinstance(vote, VoteRecord)
        assert vote.vote_type == "grace_extension"
        assert vote.grace_extension_days == 7

    def test_grace_extension_days_stored(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob", status="grace")
        vote = extend_grace_period(db, "bob", days=14, proposed_by="alice")
        row = db.get_vote(vote.vote_id)
        assert row["grace_extension_days"] == 14

    def test_target_not_in_grace_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        with pytest.raises(ValueError, match="not in grace status"):
            extend_grace_period(db, "bob", days=7, proposed_by="alice")

    def test_nonpositive_days_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob", status="grace")
        with pytest.raises(ValueError, match="positive integer"):
            extend_grace_period(db, "bob", days=0, proposed_by="alice")

    def test_duplicate_open_extension_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob", status="grace")
        extend_grace_period(db, "bob", days=7, proposed_by="alice")
        with pytest.raises(ValueError, match="already open"):
            extend_grace_period(db, "bob", days=7, proposed_by="alice")


# ---------------------------------------------------------------------------
# apply_grace_extension
# ---------------------------------------------------------------------------

class TestApplyGraceExtension:
    def _pass_extension_vote(self, db, target_id, days, voters):
        vote = extend_grace_period(db, target_id, days=days, proposed_by=voters[0])
        for voter in voters:
            cast_vote(db, vote.vote_id, voter, True)
        return vote

    def test_adds_days_to_grace_days(self, db):
        _add_member(db, "alice")
        _add_member(db, "carol")
        _add_member(db, "bob", status="grace")
        vote = self._pass_extension_vote(db, "bob", days=7, voters=["alice", "carol"])
        before = db.get_member("bob")["grace_days"]
        apply_grace_extension(db, vote.vote_id)
        after = db.get_member("bob")["grace_days"]
        assert after == before + 7

    def test_nonexistent_vote_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            apply_grace_extension(db, 9999)

    def test_wrong_vote_type_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob")
        _add_member(db, "carol")
        vote = propose_removal(db, "bob", "alice")
        cast_vote(db, vote.vote_id, "alice", True)
        cast_vote(db, vote.vote_id, "carol", True)
        with pytest.raises(ValueError, match="not a grace_extension vote"):
            apply_grace_extension(db, vote.vote_id)

    def test_unresolved_vote_raises(self, db):
        _add_member(db, "alice")
        _add_member(db, "bob", status="grace")
        vote = extend_grace_period(db, "bob", days=7, proposed_by="alice")
        with pytest.raises(ValueError, match="has not passed"):
            apply_grace_extension(db, vote.vote_id)
