"""Unit tests for gatekeeper/cluster/orphans.py."""

import time

import pytest

from gatekeeper.db.cluster import ClusterDB
from gatekeeper.cluster.orphans import (
    DEFAULT_ORPHAN_GRACE_DAYS,
    mark_orphan,
    cleanup_orphans,
    extend_orphan_grace,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with ClusterDB(str(tmp_path / "cluster.db")) as database:
        yield database


def _alerts():
    """Return a list that collects (node_id, message) alert tuples."""
    received = []
    def send_alert(node_id, message):
        received.append((node_id, message))
    send_alert.received = received
    return send_alert


# ---------------------------------------------------------------------------
# mark_orphan
# ---------------------------------------------------------------------------

class TestMarkOrphan:
    def test_inserts_record(self, db):
        mark_orphan(db, "frag-1", "node-A")
        row = db.get_orphan("frag-1", "node-A")
        assert row is not None
        assert row["fragment_id"] == "frag-1"
        assert row["owner_node_id"] == "node-A"
        assert row["cleaned_at"] is None

    def test_marked_orphan_at_defaults_to_now(self, db):
        before = time.time()
        mark_orphan(db, "frag-1", "node-A")
        after = time.time()
        row = db.get_orphan("frag-1", "node-A")
        assert before <= row["marked_orphan_at"] <= after

    def test_created_at_can_be_supplied(self, db):
        mark_orphan(db, "frag-1", "node-A", created_at=1_000_000.0)
        row = db.get_orphan("frag-1", "node-A")
        assert row["created_at"] == 1_000_000.0

    def test_idempotent_on_duplicate(self, db):
        mark_orphan(db, "frag-1", "node-A")
        mark_orphan(db, "frag-1", "node-A")  # must not raise
        # only one record exists
        pending = db.list_orphans(cleaned=False)
        assert len(pending) == 1

    def test_different_fragments_independent(self, db):
        mark_orphan(db, "frag-1", "node-A")
        mark_orphan(db, "frag-2", "node-A")
        assert len(db.list_orphans()) == 2


# ---------------------------------------------------------------------------
# cleanup_orphans
# ---------------------------------------------------------------------------

class TestCleanupOrphans:
    def _insert_aged_orphan(self, db, fragment_id, owner, age_days):
        """Insert an orphan whose marked_orphan_at is age_days in the past."""
        marked_at = time.time() - age_days * 86400
        db.insert_orphan(
            fragment_id=fragment_id,
            owner_node_id=owner,
            created_at=marked_at,
            marked_orphan_at=marked_at,
        )

    def test_deletes_eligible_orphan(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=31)
        deleted_ids = []
        def delete_fragment(fid):
            deleted_ids.append(fid)
            return 1024
        result = cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: True,
            delete_fragment=delete_fragment,
        )
        assert result["deleted"] == 1
        assert "frag-1" in deleted_ids
        row = db.get_orphan("frag-1", "node-A")
        assert row["cleaned_at"] is not None

    def test_skips_within_grace_period(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=5)
        result = cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: True,
            delete_fragment=lambda _: 0,
        )
        assert result["deleted"] == 0
        assert result["skipped_grace"] == 1
        assert db.get_orphan("frag-1", "node-A")["cleaned_at"] is None

    def test_skips_when_refrag_not_complete(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=60)
        result = cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: False,
            delete_fragment=lambda _: 0,
        )
        assert result["deleted"] == 0
        assert result["skipped_refrag"] == 1
        assert db.get_orphan("frag-1", "node-A")["cleaned_at"] is None

    def test_sends_alert_after_deletion(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=31)
        alert = _alerts()
        cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: True,
            delete_fragment=lambda _: 512,
            send_alert=alert,
        )
        assert len(alert.received) == 1
        node_id, msg = alert.received[0]
        assert node_id == "node-A"
        assert "frag-1" in msg

    def test_no_alert_when_send_alert_is_none(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=31)
        # Must not raise even without send_alert
        cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: True,
            delete_fragment=lambda _: 0,
            send_alert=None,
        )

    def test_already_cleaned_orphans_are_ignored(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=60)
        db.update_orphan("frag-1", "node-A", cleaned_at=time.time())
        result = cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: True,
            delete_fragment=lambda _: 0,
        )
        assert result["eligible"] == 0

    def test_returns_correct_counts_for_mixed_set(self, db):
        self._insert_aged_orphan(db, "frag-old", "node-A", age_days=60)
        self._insert_aged_orphan(db, "frag-young", "node-A", age_days=5)
        self._insert_aged_orphan(db, "frag-no-refrag", "node-B", age_days=45)
        result = cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda fid: fid != "frag-no-refrag",
            delete_fragment=lambda _: 0,
        )
        assert result["eligible"] == 3
        assert result["deleted"] == 1
        assert result["skipped_grace"] == 1
        assert result["skipped_refrag"] == 1

    def test_invalid_grace_days_raises(self, db):
        with pytest.raises(ValueError, match="orphan_grace_days"):
            cleanup_orphans(
                db,
                orphan_grace_days=0,
                is_refrag_complete=lambda _: True,
                delete_fragment=lambda _: 0,
            )

    def test_delete_fragment_exception_is_logged_not_raised(self, db):
        self._insert_aged_orphan(db, "frag-1", "node-A", age_days=60)
        def bad_delete(_):
            raise OSError("disk gone")
        result = cleanup_orphans(
            db,
            orphan_grace_days=30,
            is_refrag_complete=lambda _: True,
            delete_fragment=bad_delete,
        )
        assert result["deleted"] == 0
        assert db.get_orphan("frag-1", "node-A")["cleaned_at"] is None


# ---------------------------------------------------------------------------
# extend_orphan_grace
# ---------------------------------------------------------------------------

class TestExtendOrphanGrace:
    def _insert_orphan_now(self, db, fragment_id, owner):
        now = time.time()
        db.insert_orphan(
            fragment_id=fragment_id,
            owner_node_id=owner,
            created_at=now,
            marked_orphan_at=now,
        )
        return now

    def test_extends_marked_orphan_at(self, db):
        before_marked = self._insert_orphan_now(db, "frag-1", "node-A")
        extend_orphan_grace(db, "node-A", days=7)
        row = db.get_orphan("frag-1", "node-A")
        expected = before_marked + 7 * 86400
        assert abs(row["marked_orphan_at"] - expected) < 2.0  # allow 2s clock drift

    def test_returns_count_of_updated_rows(self, db):
        self._insert_orphan_now(db, "frag-1", "node-A")
        self._insert_orphan_now(db, "frag-2", "node-A")
        count = extend_orphan_grace(db, "node-A", days=3)
        assert count == 2

    def test_returns_zero_for_no_pending_orphans(self, db):
        count = extend_orphan_grace(db, "node-X", days=7)
        assert count == 0

    def test_does_not_extend_already_cleaned(self, db):
        self._insert_orphan_now(db, "frag-1", "node-A")
        db.update_orphan("frag-1", "node-A", cleaned_at=time.time())
        count = extend_orphan_grace(db, "node-A", days=7)
        assert count == 0

    def test_only_extends_matching_owner(self, db):
        t0 = self._insert_orphan_now(db, "frag-1", "node-A")
        self._insert_orphan_now(db, "frag-2", "node-B")
        extend_orphan_grace(db, "node-A", days=5)
        row_a = db.get_orphan("frag-1", "node-A")
        row_b = db.get_orphan("frag-2", "node-B")
        assert row_a["marked_orphan_at"] > t0 + 5 * 86400 - 2
        assert abs(row_b["marked_orphan_at"] - t0) < 2.0  # unchanged

    def test_invalid_days_raises(self, db):
        with pytest.raises(ValueError, match="positive integer"):
            extend_orphan_grace(db, "node-A", days=0)

    def test_negative_days_raises(self, db):
        with pytest.raises(ValueError):
            extend_orphan_grace(db, "node-A", days=-1)
