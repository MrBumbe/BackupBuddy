"""Unit tests for gatekeeper/db/cluster.py."""

import sys
import os
import time

import pytest

from gatekeeper.db.cluster import ClusterDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with ClusterDB(str(tmp_path / "cluster.db")) as database:
        yield database


def _ts() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# members
# ---------------------------------------------------------------------------

class TestMembers:
    def test_insert_and_get(self, db):
        db.insert_member("node-1", "Alice", "alice.tailnet", _ts())
        m = db.get_member("node-1")
        assert m is not None
        assert m["node_id"] == "node-1"
        assert m["display_name"] == "Alice"
        assert m["tailscale_hostname"] == "alice.tailnet"
        assert m["status"] == "active"
        assert m["contribution_bytes"] == 0
        assert m["usage_bytes"] == 0

    def test_get_unknown_returns_none(self, db):
        assert db.get_member("ghost") is None

    def test_list_all(self, db):
        db.insert_member("n1", "A", "a.ts", _ts())
        db.insert_member("n2", "B", "b.ts", _ts())
        assert len(db.list_members()) == 2

    def test_list_filters_by_status(self, db):
        db.insert_member("n1", "A", "a.ts", _ts(), status="active")
        db.insert_member("n2", "B", "b.ts", _ts(), status="grace")
        active = db.list_members(status="active")
        assert len(active) == 1
        assert active[0]["node_id"] == "n1"

    def test_update_status(self, db):
        db.insert_member("n1", "A", "a.ts", _ts())
        db.update_member("n1", status="grace")
        assert db.get_member("n1")["status"] == "grace"

    def test_update_bytes(self, db):
        db.insert_member("n1", "A", "a.ts", _ts())
        db.update_member("n1", contribution_bytes=1024, usage_bytes=512)
        m = db.get_member("n1")
        assert m["contribution_bytes"] == 1024
        assert m["usage_bytes"] == 512

    def test_update_unknown_field_raises(self, db):
        db.insert_member("n1", "A", "a.ts", _ts())
        with pytest.raises(ValueError, match="Unknown fields"):
            db.update_member("n1", nonexistent_col="bad")

    def test_update_no_fields_is_noop(self, db):
        db.insert_member("n1", "A", "a.ts", _ts())
        db.update_member("n1")  # must not raise


# ---------------------------------------------------------------------------
# invites
# ---------------------------------------------------------------------------

class TestInvites:
    def test_insert_and_get(self, db):
        now = _ts()
        db.insert_invite("coffee-mug-3", "n1", now, now + 172800)
        inv = db.get_invite("coffee-mug-3")
        assert inv is not None
        assert inv["code"] == "coffee-mug-3"
        assert inv["created_by"] == "n1"
        assert inv["used"] == 0
        assert inv["revoked"] == 0

    def test_get_unknown_returns_none(self, db):
        assert db.get_invite("no-such-code") is None

    def test_list_invites(self, db):
        now = _ts()
        db.insert_invite("a-b-1", "n1", now, now + 1)
        db.insert_invite("c-d-2", "n1", now + 1, now + 2)
        assert len(db.list_invites()) == 2

    def test_mark_used(self, db):
        now = _ts()
        db.insert_invite("x-y-5", "n1", now, now + 1)
        db.update_invite("x-y-5", used=1)
        assert db.get_invite("x-y-5")["used"] == 1

    def test_mark_revoked(self, db):
        now = _ts()
        db.insert_invite("x-y-6", "n1", now, now + 1)
        db.update_invite("x-y-6", revoked=1)
        assert db.get_invite("x-y-6")["revoked"] == 1

    def test_update_unknown_field_raises(self, db):
        now = _ts()
        db.insert_invite("a-b-9", "n1", now, now + 1)
        with pytest.raises(ValueError, match="Unknown fields"):
            db.update_invite("a-b-9", nonexistent_col="bad")

    def test_update_no_fields_is_noop(self, db):
        now = _ts()
        db.insert_invite("q-w-1", "n1", now, now + 1)
        db.update_invite("q-w-1")  # must not raise


# ---------------------------------------------------------------------------
# votes
# ---------------------------------------------------------------------------

class TestVotes:
    def test_insert_returns_positive_id(self, db):
        vid = db.insert_vote("removal", "node-2", "node-1", _ts(), _ts() + 172800)
        assert isinstance(vid, int)
        assert vid > 0

    def test_get_vote(self, db):
        now = _ts()
        vid = db.insert_vote("removal", "node-2", "node-1", now, now + 1)
        v = db.get_vote(vid)
        assert v is not None
        assert v["vote_type"] == "removal"
        assert v["target_node_id"] == "node-2"
        assert v["proposed_by"] == "node-1"
        assert v["votes_yes"] == 0
        assert v["votes_no"] == 0
        assert v["resolved"] == 0

    def test_get_unknown_returns_none(self, db):
        assert db.get_vote(99999) is None

    def test_list_all_votes(self, db):
        now = _ts()
        db.insert_vote("removal", "n2", "n1", now, now + 1)
        db.insert_vote("grace_extension", "n3", "n1", now + 1, now + 2)
        assert len(db.list_votes()) == 2

    def test_list_open_votes(self, db):
        now = _ts()
        vid1 = db.insert_vote("removal", "n2", "n1", now, now + 1)
        db.insert_vote("removal", "n3", "n1", now, now + 1)
        db.update_vote(vid1, resolved=1)
        open_votes = db.list_votes(resolved=False)
        assert len(open_votes) == 1
        assert open_votes[0]["target_node_id"] == "n3"

    def test_update_vote_counts(self, db):
        vid = db.insert_vote("removal", "n2", "n1", _ts(), _ts() + 1)
        db.update_vote(vid, votes_yes=2, votes_no=1)
        v = db.get_vote(vid)
        assert v["votes_yes"] == 2
        assert v["votes_no"] == 1

    def test_resolve_vote(self, db):
        vid = db.insert_vote("grace_extension", "n2", "n1", _ts(), _ts() + 1)
        db.update_vote(vid, resolved=1)
        assert db.get_vote(vid)["resolved"] == 1

    def test_update_unknown_field_raises(self, db):
        vid = db.insert_vote("removal", "n2", "n1", _ts(), _ts() + 1)
        with pytest.raises(ValueError, match="Unknown fields"):
            db.update_vote(vid, vote_type="bad")

    def test_update_no_fields_is_noop(self, db):
        vid = db.insert_vote("removal", "n2", "n1", _ts(), _ts() + 1)
        db.update_vote(vid)  # must not raise


# ---------------------------------------------------------------------------
# orphan_tags
# ---------------------------------------------------------------------------

class TestOrphanTags:
    def test_insert_and_get(self, db):
        now = _ts()
        db.insert_orphan("frag-abc", "node-2", now - 100, now)
        o = db.get_orphan("frag-abc", "node-2")
        assert o is not None
        assert o["fragment_id"] == "frag-abc"
        assert o["owner_node_id"] == "node-2"
        assert o["cleaned_at"] is None

    def test_get_unknown_returns_none(self, db):
        assert db.get_orphan("no-such", "no-node") is None

    def test_list_all_orphans(self, db):
        now = _ts()
        db.insert_orphan("f1", "n1", now, now)
        db.insert_orphan("f2", "n1", now, now)
        assert len(db.list_orphans()) == 2

    def test_list_pending_orphans(self, db):
        now = _ts()
        db.insert_orphan("f1", "n1", now, now)
        db.insert_orphan("f2", "n1", now, now)
        db.update_orphan("f1", "n1", cleaned_at=now + 10)
        pending = db.list_orphans(cleaned=False)
        assert len(pending) == 1
        assert pending[0]["fragment_id"] == "f2"

    def test_list_cleaned_orphans(self, db):
        now = _ts()
        db.insert_orphan("f1", "n1", now, now)
        db.insert_orphan("f2", "n1", now, now)
        db.update_orphan("f1", "n1", cleaned_at=now + 10)
        cleaned = db.list_orphans(cleaned=True)
        assert len(cleaned) == 1
        assert cleaned[0]["fragment_id"] == "f1"

    def test_mark_cleaned(self, db):
        now = _ts()
        db.insert_orphan("fx", "n1", now, now)
        db.update_orphan("fx", "n1", cleaned_at=now + 100)
        o = db.get_orphan("fx", "n1")
        assert o["cleaned_at"] == pytest.approx(now + 100, rel=1e-6)

    def test_composite_primary_key_prevents_duplicate(self, db):
        now = _ts()
        db.insert_orphan("fx", "n1", now, now)
        with pytest.raises(Exception):
            db.insert_orphan("fx", "n1", now, now)

    def test_update_unknown_field_raises(self, db):
        now = _ts()
        db.insert_orphan("fy", "n1", now, now)
        with pytest.raises(ValueError, match="Unknown fields"):
            db.update_orphan("fy", "n1", nonexistent_col="bad")

    def test_update_no_fields_is_noop(self, db):
        now = _ts()
        db.insert_orphan("fz", "n1", now, now)
        db.update_orphan("fz", "n1")  # must not raise


# ---------------------------------------------------------------------------
# File permissions
# ---------------------------------------------------------------------------

class TestPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions not enforced on Windows")
    def test_db_file_has_0600_permissions(self, tmp_path):
        db_path = str(tmp_path / "perm.db")
        with ClusterDB(db_path):
            pass
        mode = os.stat(db_path).st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# Migration system
# ---------------------------------------------------------------------------

class TestMigrations:
    def test_schema_version_is_at_least_one_after_init(self, tmp_path):
        with ClusterDB(str(tmp_path / "cluster.db")) as cdb:
            version = cdb._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0]
        assert version >= 1

    def test_reopen_preserves_data(self, tmp_path):
        db_path = str(tmp_path / "cluster.db")
        now = _ts()
        with ClusterDB(db_path) as cdb:
            cdb.insert_member("n1", "Alice", "alice.ts", now)
        with ClusterDB(db_path) as cdb2:
            assert cdb2.get_member("n1") is not None

    def test_catalog_migration_not_applied_to_cluster_db(self, tmp_path):
        with ClusterDB(str(tmp_path / "cluster.db")) as cdb:
            tables = {
                row[0] for row in cdb._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "members" in tables
        assert "files" not in tables

    def test_cluster_migration_not_applied_to_catalog_db(self, tmp_path):
        from gatekeeper.db.catalog import CatalogDB
        with CatalogDB(str(tmp_path / "catalog.db"), os.urandom(32)) as cdb:
            tables = {
                row[0] for row in cdb._conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "files" in tables
        assert "members" not in tables
