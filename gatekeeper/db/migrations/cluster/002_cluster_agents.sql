-- Migration 002: agent registry and lifeboat distribution tracking.
--
-- agents          — agents that have registered with this gatekeeper,
--                   including the URL for pushing lifeboat bundles
-- lifeboat_status — history of distribution runs (most recent first)

CREATE TABLE IF NOT EXISTS agents (
    agent_name    TEXT PRIMARY KEY,
    ip            TEXT NOT NULL,
    lifeboat_url  TEXT,           -- e.g. http://192.168.1.101:8082/lifeboat
    registered_at REAL NOT NULL,
    last_seen     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS lifeboat_status (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    distributed_at REAL    NOT NULL,
    agent_count    INTEGER NOT NULL,
    success_count  INTEGER NOT NULL,
    status         TEXT    NOT NULL   -- 'ok', 'partial', 'failed'
);

CREATE INDEX IF NOT EXISTS idx_lifeboat_recent ON lifeboat_status (distributed_at DESC);
