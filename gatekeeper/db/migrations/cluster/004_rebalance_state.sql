-- Migration 004: rebalance_state singleton
-- Tracks cluster-size baseline and stability for the re-fragmentation scheduler.
-- The CHECK(id = 1) constraint enforces exactly one row.

CREATE TABLE IF NOT EXISTS rebalance_state (
    id                    INTEGER PRIMARY KEY CHECK(id = 1),
    baseline_count        INTEGER NOT NULL DEFAULT 0,
    current_tracked_count INTEGER NOT NULL DEFAULT 0,
    size_stable_since     REAL    NOT NULL DEFAULT 0.0,
    last_run_at           REAL,
    in_progress           INTEGER NOT NULL DEFAULT 0 CHECK(in_progress IN (0, 1))
);

INSERT OR IGNORE INTO rebalance_state
    (id, baseline_count, current_tracked_count, size_stable_since)
VALUES
    (1, 0, 0, 0.0);
