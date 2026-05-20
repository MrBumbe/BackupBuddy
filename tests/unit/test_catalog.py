"""Unit tests for gatekeeper/db/catalog.py."""

import os
import sys
import time

import pytest

from gatekeeper.db.catalog import CatalogDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def key() -> bytes:
    return os.urandom(32)


@pytest.fixture
def db(tmp_path, key):
    with CatalogDB(str(tmp_path / "catalog.db"), key) as database:
        yield database


# ---------------------------------------------------------------------------
# Insert and retrieve
# ---------------------------------------------------------------------------

class TestInsertAndRetrieve:
    def test_insert_returns_positive_int(self, db):
        fid = db.insert_file("URI:CHK:abc", "a" * 64, "/home/user/file.txt", "agent1", time.time(), 512, "lagom", 3, 5)
        assert isinstance(fid, int)
        assert fid > 0

    def test_get_file_by_path_returns_decrypted_values(self, db):
        ts = time.time()
        db.insert_file("URI:CHK:abc", "b" * 64, "/home/user/doc.pdf", "agent1", ts, 1024, "lagom", 3, 5)
        row = db.get_file_by_path("agent1", "/home/user/doc.pdf")
        assert row is not None
        assert row["cap"] == "URI:CHK:abc"
        assert row["original_path"] == "/home/user/doc.pdf"
        assert row["agent"] == "agent1"
        assert row["sha256"] == "b" * 64
        assert row["profile"] == "lagom"
        assert row["k"] == 3
        assert row["n"] == 5

    def test_get_file_by_path_wrong_agent_returns_none(self, db):
        db.insert_file("URI:CHK:abc", "c" * 64, "/home/user/doc.pdf", "agent1", time.time(), 1, "lagom", 3, 5)
        assert db.get_file_by_path("agent2", "/home/user/doc.pdf") is None

    def test_get_file_by_path_unknown_path_returns_none(self, db):
        assert db.get_file_by_path("agent1", "/not/there.txt") is None

    def test_get_file_by_path_returns_most_recent_when_duplicates(self, db):
        ts = time.time()
        db.insert_file("URI:CHK:old", "d" * 64, "/dup.txt", "ag", ts - 100, 1, "lagom", 3, 5)
        db.insert_file("URI:CHK:new", "e" * 64, "/dup.txt", "ag", ts, 1, "lagom", 3, 5)
        row = db.get_file_by_path("ag", "/dup.txt")
        assert row["cap"] == "URI:CHK:new"

    def test_path_hmac_not_exposed_in_returned_dict(self, db):
        db.insert_file("URI:CHK:x", "f" * 64, "/a/b.txt", "ag", time.time(), 1, "trygg", 3, 7)
        row = db.get_file_by_path("ag", "/a/b.txt")
        assert "path_hmac" not in row


# ---------------------------------------------------------------------------
# get_all_files / get_files_since
# ---------------------------------------------------------------------------

class TestGetAll:
    def test_get_all_files_returns_all_rows(self, db):
        ts = time.time()
        db.insert_file("URI:CHK:1", "g" * 64, "/p1.txt", "ag", ts, 1, "lagom", 3, 5)
        db.insert_file("URI:CHK:2", "h" * 64, "/p2.txt", "ag", ts + 1, 2, "lagom", 3, 5)
        assert len(db.get_all_files()) == 2

    def test_get_all_files_empty_returns_empty_list(self, db):
        assert db.get_all_files() == []

    def test_get_files_since_filters_by_timestamp(self, db):
        now = time.time()
        db.insert_file("URI:CHK:old", "i" * 64, "/old.txt", "ag", now - 100, 1, "lagom", 3, 5)
        db.insert_file("URI:CHK:new", "j" * 64, "/new.txt", "ag", now + 100, 1, "lagom", 3, 5)
        recent = db.get_files_since(now)
        assert len(recent) == 1
        assert recent[0]["cap"] == "URI:CHK:new"

    def test_get_files_since_returns_empty_when_nothing_qualifies(self, db):
        db.insert_file("URI:CHK:old", "k" * 64, "/old.txt", "ag", time.time() - 100, 1, "lagom", 3, 5)
        assert db.get_files_since(time.time()) == []


# ---------------------------------------------------------------------------
# update_file
# ---------------------------------------------------------------------------

class TestUpdate:
    def test_update_sha256(self, db):
        fid = db.insert_file("URI:CHK:u", "l" * 64, "/up.txt", "ag", time.time(), 1, "lagom", 3, 5)
        new_hash = "m" * 64
        db.update_file(fid, sha256=new_hash)
        row = db.get_file_by_path("ag", "/up.txt")
        assert row["sha256"] == new_hash

    def test_update_cap_re_encrypts(self, db):
        fid = db.insert_file("URI:CHK:old_cap", "n" * 64, "/cap.txt", "ag", time.time(), 1, "lagom", 3, 5)
        db.update_file(fid, cap="URI:CHK:new_cap")
        row = db.get_file_by_path("ag", "/cap.txt")
        assert row["cap"] == "URI:CHK:new_cap"

    def test_update_profile_and_kn(self, db):
        fid = db.insert_file("URI:CHK:v", "o" * 64, "/v.txt", "ag", time.time(), 1, "lagom", 3, 5)
        db.update_file(fid, profile="trygg", k=3, n=7)
        row = db.get_file_by_path("ag", "/v.txt")
        assert row["profile"] == "trygg"
        assert row["n"] == 7

    def test_update_unknown_field_raises_value_error(self, db):
        fid = db.insert_file("URI:CHK:z", "p" * 64, "/z.txt", "ag", time.time(), 1, "lagom", 3, 5)
        with pytest.raises(ValueError, match="Unknown fields"):
            db.update_file(fid, nonexistent_column="bad")

    def test_update_with_no_fields_is_noop(self, db):
        fid = db.insert_file("URI:CHK:noop", "q" * 64, "/noop.txt", "ag", time.time(), 1, "lagom", 3, 5)
        db.update_file(fid)  # no exception, no change
        row = db.get_file_by_path("ag", "/noop.txt")
        assert row is not None


# ---------------------------------------------------------------------------
# delete_file
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_removes_row(self, db):
        fid = db.insert_file("URI:CHK:del", "r" * 64, "/del.txt", "ag", time.time(), 1, "lagom", 3, 5)
        db.delete_file(fid)
        assert db.get_file_by_path("ag", "/del.txt") is None

    def test_delete_nonexistent_id_is_noop(self, db):
        db.delete_file(99999)  # must not raise


# ---------------------------------------------------------------------------
# Encryption at rest
# ---------------------------------------------------------------------------

class TestEncryptionAtRest:
    def test_cap_not_stored_in_plaintext(self, tmp_path):
        db_path = str(tmp_path / "enc_cap.db")
        with CatalogDB(db_path, os.urandom(32)) as test_db:
            test_db.insert_file("URI:CHK:SUPERSECRET", "s" * 64, "/f.txt", "ag", time.time(), 1, "lagom", 3, 5)
        raw = open(db_path, "rb").read()
        assert b"SUPERSECRET" not in raw

    def test_path_not_stored_in_plaintext(self, tmp_path):
        db_path = str(tmp_path / "enc_path.db")
        with CatalogDB(db_path, os.urandom(32)) as test_db:
            test_db.insert_file("URI:CHK:x", "t" * 64, "/home/anders/SECRETPATH/file.txt", "ag", time.time(), 1, "lagom", 3, 5)
        raw = open(db_path, "rb").read()
        assert b"SECRETPATH" not in raw

    def test_wrong_key_cannot_decrypt(self, tmp_path):
        db_path = str(tmp_path / "wrong_key.db")
        key_a = os.urandom(32)
        key_b = os.urandom(32)
        with CatalogDB(db_path, key_a) as db_a:
            db_a.insert_file("URI:CHK:secret", "u" * 64, "/secret.txt", "ag", time.time(), 1, "lagom", 3, 5)
        with CatalogDB(db_path, key_b) as db_b:
            with pytest.raises(Exception):
                db_b.get_all_files()


# ---------------------------------------------------------------------------
# NULL original_path (ADR-008 call-home edge case)
# ---------------------------------------------------------------------------

class TestNullOriginalPath:
    def test_insert_with_null_path_succeeds(self, db):
        # ADR-008: call-home reconstruction may produce entries without a known path.
        fid = db.insert_file("URI:CHK:null", "v" * 64, None, "ag", time.time(), 1, "lagom", 3, 5)
        assert fid > 0

    def test_get_all_includes_null_path_entries(self, db):
        db.insert_file("URI:CHK:null", "w" * 64, None, "ag", time.time(), 1, "lagom", 3, 5)
        rows = db.get_all_files()
        assert any(r["original_path"] is None for r in rows)

    def test_get_file_by_path_does_not_return_null_path_rows(self, db):
        db.insert_file("URI:CHK:null", "x" * 64, None, "ag", time.time(), 1, "lagom", 3, 5)
        assert db.get_file_by_path("ag", "/some/path.txt") is None


# ---------------------------------------------------------------------------
# File permissions
# ---------------------------------------------------------------------------

class TestPermissions:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions not enforced on Windows")
    def test_db_file_has_0600_permissions(self, tmp_path):
        db_path = str(tmp_path / "perm.db")
        with CatalogDB(db_path, os.urandom(32)):
            pass
        mode = os.stat(db_path).st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# Migration system
# ---------------------------------------------------------------------------

class TestMigrations:
    def test_schema_version_is_at_least_one_after_init(self, tmp_path):
        db_path = str(tmp_path / "mig.db")
        with CatalogDB(db_path, os.urandom(32)) as test_db:
            version = test_db._conn.execute(
                "SELECT version FROM schema_version"
            ).fetchone()[0]
        assert version >= 1

    def test_reopen_preserves_data_and_does_not_rerun_migrations(self, tmp_path):
        key = os.urandom(32)
        db_path = str(tmp_path / "reopen.db")
        with CatalogDB(db_path, key) as db1:
            db1.insert_file("URI:CHK:r", "y" * 64, "/reopen.txt", "ag", time.time(), 1, "lagom", 3, 5)
        with CatalogDB(db_path, key) as db2:
            assert len(db2.get_all_files()) == 1

    def test_invalid_key_length_raises(self, tmp_path):
        with pytest.raises(ValueError, match="32 bytes"):
            CatalogDB(str(tmp_path / "bad.db"), b"tooshort")
