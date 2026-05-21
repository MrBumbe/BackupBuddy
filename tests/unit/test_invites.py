"""Unit tests for gatekeeper/cluster/invites.py."""

import re
import time

import pytest

from gatekeeper.db.cluster import ClusterDB
from gatekeeper.cluster.invites import (
    EXPIRY_SECONDS,
    InviteCode,
    _generate_code,
    _load_words,
    consume_invite,
    generate_invite,
    revoke_invite,
    validate_invite,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    with ClusterDB(str(tmp_path / "cluster.db")) as database:
        yield database


@pytest.fixture
def missing_wordlist(tmp_path):
    """Path to a wordlist file that does not exist."""
    return tmp_path / "nonexistent_wordlist.txt"


@pytest.fixture
def custom_wordlist(tmp_path):
    """Minimal wordlist with predictable words for format testing."""
    path = tmp_path / "wordlist.txt"
    path.write_text("alpha\nbeta\ngamma\ndelta\n", encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Wordlist loading
# ---------------------------------------------------------------------------

class TestLoadWords:
    def test_loads_bundled_wordlist(self):
        words = _load_words()
        assert len(words) > 100
        assert all(isinstance(w, str) and w for w in words)

    def test_missing_file_returns_empty_list(self, missing_wordlist):
        assert _load_words(missing_wordlist) == []

    def test_custom_wordlist(self, custom_wordlist):
        words = _load_words(custom_wordlist)
        assert words == ["alpha", "beta", "gamma", "delta"]


# ---------------------------------------------------------------------------
# Code generation
# ---------------------------------------------------------------------------

class TestGenerateCode:
    _PATTERN = re.compile(r"^[a-z]+-[a-z]+-[1-9]$")
    _FALLBACK = re.compile(r"^bb-[0-9a-f]{8}$")

    def test_format_with_wordlist(self, custom_wordlist):
        code = _generate_code(custom_wordlist)
        assert re.match(r"^[a-z]+-[a-z]+-[1-9]$", code), f"Unexpected format: {code}"

    def test_words_from_wordlist(self, custom_wordlist):
        valid_words = {"alpha", "beta", "gamma", "delta"}
        for _ in range(20):
            code = _generate_code(custom_wordlist)
            parts = code.rsplit("-", 1)
            word_part = parts[0]
            w1, w2 = word_part.split("-", 1)
            assert w1 in valid_words, f"Unknown word: {w1}"
            assert w2 in valid_words, f"Unknown word: {w2}"

    def test_number_in_range_1_to_9(self, custom_wordlist):
        seen = set()
        for _ in range(200):
            code = _generate_code(custom_wordlist)
            n = int(code.rsplit("-", 1)[1])
            assert 1 <= n <= 9
            seen.add(n)
        assert len(seen) > 1, "Number appears non-random"

    def test_fallback_format(self, missing_wordlist):
        code = _generate_code(missing_wordlist)
        assert re.match(r"^bb-[0-9a-f]{8}$", code), f"Unexpected fallback: {code}"

    def test_codes_differ_across_calls(self, custom_wordlist):
        codes = {_generate_code(custom_wordlist) for _ in range(30)}
        assert len(codes) > 1, "All generated codes were identical"

    def test_bundled_wordlist_produces_valid_format(self):
        code = _generate_code()
        assert re.match(r"^[a-z]+-[a-z]+-[1-9]$", code), f"Unexpected format: {code}"


# ---------------------------------------------------------------------------
# generate_invite
# ---------------------------------------------------------------------------

class TestGenerateInvite:
    def test_returns_invite_code(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        assert isinstance(invite, InviteCode)
        assert invite.created_by == "alice"
        assert not invite.used
        assert not invite.revoked

    def test_stored_in_db(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        row = db.get_invite(invite.code)
        assert row is not None
        assert row["created_by"] == "alice"
        assert row["used"] == 0
        assert row["revoked"] == 0

    def test_expires_at_48h(self, db, custom_wordlist):
        before = time.time()
        invite = generate_invite(db, "bob", wordlist_path=custom_wordlist)
        after = time.time()
        assert before + EXPIRY_SECONDS <= invite.expires_at <= after + EXPIRY_SECONDS


# ---------------------------------------------------------------------------
# validate_invite
# ---------------------------------------------------------------------------

class TestValidateInvite:
    def test_valid_code_returns_invite(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        result = validate_invite(db, invite.code)
        assert result is not None
        assert result.code == invite.code

    def test_unknown_code_returns_none(self, db):
        assert validate_invite(db, "does-not-exist-1") is None

    def test_revoked_code_returns_none(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        db.update_invite(invite.code, revoked=1)
        assert validate_invite(db, invite.code) is None

    def test_used_code_returns_none(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        db.update_invite(invite.code, used=1)
        assert validate_invite(db, invite.code) is None

    def test_expired_code_returns_none(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        # Back-date expires_at to 1 second ago
        db._conn.execute(
            "UPDATE invites SET expires_at = ? WHERE code = ?",
            (time.time() - 1, invite.code),
        )
        db._conn.commit()
        assert validate_invite(db, invite.code) is None

    def test_revoked_checked_before_expiry(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        db._conn.execute(
            "UPDATE invites SET revoked = 1, expires_at = ? WHERE code = ?",
            (time.time() - 1, invite.code),
        )
        db._conn.commit()
        assert validate_invite(db, invite.code) is None

    def test_used_checked_before_expiry(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        db._conn.execute(
            "UPDATE invites SET used = 1, expires_at = ? WHERE code = ?",
            (time.time() - 1, invite.code),
        )
        db._conn.commit()
        assert validate_invite(db, invite.code) is None


# ---------------------------------------------------------------------------
# revoke_invite
# ---------------------------------------------------------------------------

class TestRevokeInvite:
    def test_revoke_valid_code(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        revoke_invite(db, invite.code, revoked_by="bob")
        assert validate_invite(db, invite.code) is None
        row = db.get_invite(invite.code)
        assert row["revoked"] == 1

    def test_revoke_unknown_code_raises(self, db):
        with pytest.raises(ValueError, match="not found"):
            revoke_invite(db, "ghost-code-1", revoked_by="alice")

    def test_revoke_used_code_raises(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        db.update_invite(invite.code, used=1)
        with pytest.raises(ValueError, match="used"):
            revoke_invite(db, invite.code, revoked_by="bob")


# ---------------------------------------------------------------------------
# consume_invite
# ---------------------------------------------------------------------------

class TestConsumeInvite:
    def test_consume_valid_code(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        result = consume_invite(db, invite.code)
        assert result.used is True
        row = db.get_invite(invite.code)
        assert row["used"] == 1

    def test_consume_marks_code_unusable(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        consume_invite(db, invite.code)
        assert validate_invite(db, invite.code) is None

    def test_consume_invalid_code_raises(self, db):
        with pytest.raises(ValueError, match="Invalid"):
            consume_invite(db, "no-such-code-3")

    def test_consume_expired_code_raises(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        db._conn.execute(
            "UPDATE invites SET expires_at = ? WHERE code = ?",
            (time.time() - 1, invite.code),
        )
        db._conn.commit()
        with pytest.raises(ValueError):
            consume_invite(db, invite.code)

    def test_consume_revoked_code_raises(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        revoke_invite(db, invite.code, revoked_by="bob")
        with pytest.raises(ValueError):
            consume_invite(db, invite.code)

    def test_double_consume_raises(self, db, custom_wordlist):
        invite = generate_invite(db, "alice", wordlist_path=custom_wordlist)
        consume_invite(db, invite.code)
        with pytest.raises(ValueError):
            consume_invite(db, invite.code)
