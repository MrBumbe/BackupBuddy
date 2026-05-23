-- Migration 005: add share_log flag to agents table.
--
-- share_log is set by the agent at registration time.  When true the
-- gatekeeper UI may display per-file backup events for that agent.
-- Defaults to 0 (false) so existing rows opt out automatically.

ALTER TABLE agents ADD COLUMN share_log INTEGER NOT NULL DEFAULT 0
    CHECK(share_log IN (0, 1));
