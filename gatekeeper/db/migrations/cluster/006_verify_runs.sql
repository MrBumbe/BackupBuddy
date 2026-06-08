-- Migration 006: add verify_runs table.
--
-- Persists the result of every nightly (and on-demand) verification run.
-- detail_json stores per-layer counts only (ok, warnings, errors) — never
-- raw exception strings or Tahoe capability material.

CREATE TABLE IF NOT EXISTS verify_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at       REAL    NOT NULL,
    result       TEXT    NOT NULL CHECK(result IN ('passed', 'failed')),
    detail_json  TEXT    NOT NULL DEFAULT '{}',
    triggered_by TEXT    NOT NULL DEFAULT 'scheduler'
);
