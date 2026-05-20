"""Cluster database access layer.

Stores cluster membership, invite codes, removal votes, and orphan fragment
tracking in SQLite (WAL mode, permissions 0600).

No columns are encrypted: cluster.db contains operational metadata (node IDs,
invite codes, vote counts) — not cryptographic key material.  File caps and
original paths are handled by catalog.py.
"""

import logging
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MIGRATIONS_DIR = Path(__file__).parent / "migrations" / "cluster"

_MEMBER_UPDATABLE = frozenset({
    "display_name", "tailscale_hostname",
    "contribution_bytes", "usage_bytes", "profile", "status",
})

_INVITE_UPDATABLE = frozenset({"used", "revoked"})

_VOTE_UPDATABLE = frozenset({"votes_yes", "votes_no", "resolved"})

_ORPHAN_UPDATABLE = frozenset({"cleaned_at"})


class ClusterDB:
    """SQLite-backed store for cluster state.

    Covers: members, invites, votes, orphan_tags.
    Call close() when done, or use as a context manager.
    """

    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")

        if sys.platform != "win32":
            os.chmod(db_path, stat.S_IRUSR | stat.S_IWUSR)

        self._run_migrations()
        logger.info("ClusterDB opened at %s", db_path)

    # ------------------------------------------------------------------
    # Migration runner
    # ------------------------------------------------------------------

    def _run_migrations(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version "
            "(version INTEGER NOT NULL DEFAULT 0)"
        )
        if self._conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self._conn.execute("INSERT INTO schema_version (version) VALUES (0)")
        self._conn.commit()

        current: int = self._conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()[0]

        migration_files = sorted(
            f for f in _MIGRATIONS_DIR.glob("*.sql")
            if re.match(r"^\d+_", f.name)
        )

        for mf in migration_files:
            num = int(mf.name.split("_")[0])
            if num <= current:
                continue
            logger.info("Applying cluster migration %s", mf.name)
            self._conn.executescript(mf.read_text(encoding="utf-8"))
            self._conn.execute("UPDATE schema_version SET version = ?", (num,))
            self._conn.commit()
            logger.info("ClusterDB schema now at version %d", num)

    # ------------------------------------------------------------------
    # members
    # ------------------------------------------------------------------

    def insert_member(
        self,
        node_id: str,
        display_name: str,
        tailscale_hostname: str,
        joined_at: float,
        contribution_bytes: int = 0,
        usage_bytes: int = 0,
        profile: str = "lagom",
        status: str = "active",
    ) -> None:
        self._conn.execute(
            "INSERT INTO members "
            "(node_id, display_name, tailscale_hostname, joined_at, "
            "contribution_bytes, usage_bytes, profile, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (node_id, display_name, tailscale_hostname, joined_at,
             contribution_bytes, usage_bytes, profile, status),
        )
        self._conn.commit()

    def get_member(self, node_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM members WHERE node_id = ?", (node_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_members(self, status: str | None = None) -> list[dict]:
        if status is not None:
            rows = self._conn.execute(
                "SELECT * FROM members WHERE status = ? ORDER BY joined_at",
                (status,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM members ORDER BY joined_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_member(self, node_id: str, **fields: Any) -> None:
        _check_fields(fields, _MEMBER_UPDATABLE)
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        self._conn.execute(  # noqa: S608
            f"UPDATE members SET {set_clause} WHERE node_id = ?",  # noqa: S608
            (*fields.values(), node_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # invites
    # ------------------------------------------------------------------

    def insert_invite(
        self,
        code: str,
        created_by: str,
        created_at: float,
        expires_at: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO invites (code, created_by, created_at, expires_at) "
            "VALUES (?, ?, ?, ?)",
            (code, created_by, created_at, expires_at),
        )
        self._conn.commit()

    def get_invite(self, code: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM invites WHERE code = ?", (code,)
        ).fetchone()
        return dict(row) if row else None

    def list_invites(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM invites ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def update_invite(self, code: str, **fields: Any) -> None:
        _check_fields(fields, _INVITE_UPDATABLE)
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        self._conn.execute(  # noqa: S608
            f"UPDATE invites SET {set_clause} WHERE code = ?",  # noqa: S608
            (*fields.values(), code),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # votes
    # ------------------------------------------------------------------

    def insert_vote(
        self,
        vote_type: str,
        target_node_id: str,
        proposed_by: str,
        proposed_at: float,
        closes_at: float,
    ) -> int:
        cursor = self._conn.execute(
            "INSERT INTO votes "
            "(vote_type, target_node_id, proposed_by, proposed_at, closes_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (vote_type, target_node_id, proposed_by, proposed_at, closes_at),
        )
        self._conn.commit()
        return cursor.lastrowid

    def get_vote(self, vote_id: int) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM votes WHERE id = ?", (vote_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_votes(self, resolved: bool | None = None) -> list[dict]:
        if resolved is not None:
            rows = self._conn.execute(
                "SELECT * FROM votes WHERE resolved = ? ORDER BY proposed_at DESC",
                (1 if resolved else 0,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM votes ORDER BY proposed_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_vote(self, vote_id: int, **fields: Any) -> None:
        _check_fields(fields, _VOTE_UPDATABLE)
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        self._conn.execute(  # noqa: S608
            f"UPDATE votes SET {set_clause} WHERE id = ?",  # noqa: S608
            (*fields.values(), vote_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # orphan_tags
    # ------------------------------------------------------------------

    def insert_orphan(
        self,
        fragment_id: str,
        owner_node_id: str,
        created_at: float,
        marked_orphan_at: float,
    ) -> None:
        self._conn.execute(
            "INSERT INTO orphan_tags "
            "(fragment_id, owner_node_id, created_at, marked_orphan_at) "
            "VALUES (?, ?, ?, ?)",
            (fragment_id, owner_node_id, created_at, marked_orphan_at),
        )
        self._conn.commit()

    def get_orphan(self, fragment_id: str, owner_node_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM orphan_tags WHERE fragment_id = ? AND owner_node_id = ?",
            (fragment_id, owner_node_id),
        ).fetchone()
        return dict(row) if row else None

    def list_orphans(self, cleaned: bool | None = None) -> list[dict]:
        if cleaned is True:
            rows = self._conn.execute(
                "SELECT * FROM orphan_tags WHERE cleaned_at IS NOT NULL "
                "ORDER BY marked_orphan_at"
            ).fetchall()
        elif cleaned is False:
            rows = self._conn.execute(
                "SELECT * FROM orphan_tags WHERE cleaned_at IS NULL "
                "ORDER BY marked_orphan_at"
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM orphan_tags ORDER BY marked_orphan_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def update_orphan(
        self, fragment_id: str, owner_node_id: str, **fields: Any
    ) -> None:
        _check_fields(fields, _ORPHAN_UPDATABLE)
        if not fields:
            return
        set_clause = ", ".join(f"{col} = ?" for col in fields)
        self._conn.execute(  # noqa: S608
            f"UPDATE orphan_tags SET {set_clause} "  # noqa: S608
            "WHERE fragment_id = ? AND owner_node_id = ?",
            (*fields.values(), fragment_id, owner_node_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._conn.close()
        logger.info("ClusterDB closed")

    def __enter__(self) -> "ClusterDB":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _check_fields(fields: dict, allowed: frozenset) -> None:
    unknown = set(fields) - allowed
    if unknown:
        raise ValueError(f"Unknown fields: {unknown}")
