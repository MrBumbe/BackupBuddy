"""Adaptive fragmentation profile — runtime k/n computation.

Implements the 1/3 ratio scaling described in ADR-006a:
  - node_count < 3 : all-of-n encoding (no meaningful redundancy possible)
  - node_count >= 3: n = min(node_count, max_n)
                     k = max(round(n * ratio), min_k)

See ADR-006a and ADR-018. This module is the only place that resolves the
"adaptive" profile to concrete (k, n) values.
"""

import logging

from gatekeeper.config import AdaptiveConfig

logger = logging.getLogger(__name__)


def compute_adaptive_kn(node_count: int, config: AdaptiveConfig) -> tuple[int, int]:
    """Return (k, n) for a cluster of *node_count* active nodes.

    Raises ValueError if node_count < 1.
    """
    if node_count < 1:
        raise ValueError(f"node_count must be at least 1, got {node_count}")

    if node_count < 3:
        # No redundancy possible — all fragments must be available to restore.
        return (node_count, node_count)

    n = min(node_count, config.max_n)
    k = max(round(n * config.ratio), config.min_k)
    k = min(k, n)  # k must never exceed n
    return (k, n)


def get_current_kn(db, config: AdaptiveConfig) -> tuple[int, int]:
    """Return (k, n) based on the current active cluster size from *db*.

    Counts members with status 'active' or 'grace' — grace members are still
    holding fragments and their storage remains available until purge.
    """
    active = db.list_members(status="active")
    grace = db.list_members(status="grace")
    node_count = len(active) + len(grace)

    k, n = compute_adaptive_kn(node_count, config)
    logger.debug(
        "Adaptive k/n: node_count=%d → k=%d, n=%d (ratio=%.2f, max_n=%d)",
        node_count, k, n, config.ratio, config.max_n,
    )
    return (k, n)
