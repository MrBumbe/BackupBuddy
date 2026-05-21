"""Orphan fragment tracking and cleanup.

Policy layer over ClusterDB — all SQL is handled by ClusterDB methods.

fragment_id is treated as a Tahoe file capability reference (file-cap level).
One orphan_tag per logical file, not per individual Tahoe share.  This matches
the catalog.db granularity and is the Phase 1 design (see ADR-012).

Cleanup lifecycle:
  mark_orphan  → (daily job) cleanup_orphans → fragment deleted, cleaned_at set
  extend_orphan_grace → bumps marked_orphan_at forward, extending the deadline
"""

import logging
import time
from typing import Callable

logger = logging.getLogger(__name__)

DEFAULT_ORPHAN_GRACE_DAYS: int = 30


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

def mark_orphan(
    db,
    fragment_id: str,
    owner_node_id: str,
    *,
    created_at: float | None = None,
) -> None:
    """Record a fragment as orphaned (owner node no longer in cluster).

    Idempotent: if the orphan tag already exists the call is silently ignored.
    created_at defaults to now if not supplied.
    """
    now = time.time()
    try:
        db.insert_orphan(
            fragment_id=fragment_id,
            owner_node_id=owner_node_id,
            created_at=created_at if created_at is not None else now,
            marked_orphan_at=now,
        )
        logger.info(
            "Fragment %s from node %s marked as orphan at %.0f",
            fragment_id, owner_node_id, now,
        )
    except Exception:
        existing = db.get_orphan(fragment_id, owner_node_id)
        if existing is not None:
            logger.debug(
                "Orphan tag already exists for fragment %s / node %s — skipped",
                fragment_id, owner_node_id,
            )
            return
        raise


def cleanup_orphans(
    db,
    *,
    orphan_grace_days: int,
    is_refrag_complete: Callable[[str], bool],
    delete_fragment: Callable[[str], int],
    send_alert: Callable[[str, str], None] | None = None,
) -> dict[str, int]:
    """Delete orphan fragments whose grace period has expired and re-frag is done.

    is_refrag_complete(fragment_id) -> bool  — REQUIRED; no default.
        Return True once the data that was on the orphaned node has been
        re-fragmented onto healthy nodes.  Pass ``lambda _: True`` in tests
        when re-frag is not yet implemented (task 1.11).

    delete_fragment(fragment_id) -> int  — bytes freed.
        Must delete the fragment file AND call StoragePoolManager.remove_fragment()
        so the in-memory quota counter stays accurate.

    Returns a summary dict:
        {"eligible": N, "deleted": N, "skipped_grace": N, "skipped_refrag": N}
    """
    if orphan_grace_days <= 0:
        raise ValueError("orphan_grace_days must be a positive integer")

    pending = db.list_orphans(cleaned=False)
    grace_seconds = orphan_grace_days * 86400
    now = time.time()

    counts = {"eligible": len(pending), "deleted": 0, "skipped_grace": 0, "skipped_refrag": 0}

    for row in pending:
        fragment_id   = row["fragment_id"]
        owner_node_id = row["owner_node_id"]
        age = now - row["marked_orphan_at"]

        if age < grace_seconds:
            counts["skipped_grace"] += 1
            continue

        if not is_refrag_complete(fragment_id):
            counts["skipped_refrag"] += 1
            logger.debug(
                "Orphan %s (owner %s): re-frag not complete — skipping",
                fragment_id, owner_node_id,
            )
            continue

        try:
            bytes_freed = delete_fragment(fragment_id)
        except Exception:
            logger.exception(
                "Failed to delete orphan fragment %s (owner %s)",
                fragment_id, owner_node_id,
            )
            continue

        db.update_orphan(fragment_id, owner_node_id, cleaned_at=now)
        counts["deleted"] += 1
        logger.info(
            "Orphan fragment %s (owner %s) deleted — %d bytes freed",
            fragment_id, owner_node_id, bytes_freed,
        )

        if send_alert is not None:
            send_alert(
                owner_node_id,
                f"Orphan fragment {fragment_id} has been cleaned up "
                f"({bytes_freed} bytes freed).",
            )

    logger.info(
        "Orphan cleanup complete: %s",
        ", ".join(f"{k}={v}" for k, v in counts.items()),
    )
    return counts


def extend_orphan_grace(db, owner_node_id: str, days: int) -> int:
    """Push marked_orphan_at forward by *days* for all pending orphans of owner.

    This effectively extends the grace deadline without changing grace_days config.
    Returns the number of orphan tags updated (0 if none pending — not an error).
    """
    if days <= 0:
        raise ValueError("Extension days must be a positive integer")

    pending = [
        row for row in db.list_orphans(cleaned=False)
        if row["owner_node_id"] == owner_node_id
    ]

    if not pending:
        logger.debug(
            "extend_orphan_grace: no pending orphans for node %s", owner_node_id
        )
        return 0

    extension_seconds = days * 86400
    updated = 0
    for row in pending:
        new_marked_at = row["marked_orphan_at"] + extension_seconds
        db.update_orphan(
            row["fragment_id"],
            owner_node_id,
            marked_orphan_at=new_marked_at,
        )
        updated += 1

    logger.info(
        "Grace extended by %d days for %d orphan(s) of node %s",
        days, updated, owner_node_id,
    )
    return updated
