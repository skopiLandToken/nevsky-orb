-- Migration: 2026-05-27 — Yindo Telegram bridge tables
-- Applied live in production: TBD
-- Doctrine: same Yakov, different interface. See CLAUDE.md "One Yakov, multiple interfaces".

-- ============================================================
-- Purpose
-- ------------------------------------------------------------
-- The Yindo bridge (@Yindo_skopi_bot) routes Iosif's Telegram
-- messages to a `claude --print --resume <session_id>` subprocess
-- on the host. Two tables back it:
--
-- 1) yindo_session — keyed by telegram chat_id, stores the
--    Claude Code session UUID so a single chat appends to one
--    ongoing conversation across messages. Updated_at lets us
--    detect dormant sessions if we ever want to auto-rotate.
--
-- 2) yindo_telegram_log — append-only audit log. Every inbound
--    prompt and every outbound response (including chunk
--    metadata, duration, cost) goes here. Replaces in-chat
--    approval theatre with a real audit trail: this is how
--    "bypassPermissions" stays honest.
--
-- Owner-only by hard telegram_id check at the webhook. These
-- tables therefore only ever see Iosif's chat_id (7583693994's
-- private chat with the bot), but the schema is generic in case
-- doctrine ever opens a second owner slot.
-- ============================================================

CREATE TABLE IF NOT EXISTS yindo_session (
    chat_id           BIGINT PRIMARY KEY,
    session_id        UUID NOT NULL,
    cwd               TEXT NOT NULL DEFAULT '/opt/nevsky-dev',
    turns             INTEGER NOT NULL DEFAULT 0,
    total_cost_usd    NUMERIC(10, 4) NOT NULL DEFAULT 0,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- direction: 'in' (Iosif → bot) or 'out' (bot → Iosif) or 'system' (cancel, status, error)
-- body: full message text (may be long; no truncation — audit value comes from completeness)
-- session_id: nullable because /cancel and /status arrive before a session is loaded
-- duration_ms: only populated for 'out' rows that represent a completed claude turn
-- cost_usd: only populated for 'out' rows that completed a claude turn
CREATE TABLE IF NOT EXISTS yindo_telegram_log (
    id           BIGSERIAL PRIMARY KEY,
    chat_id      BIGINT NOT NULL,
    direction    TEXT NOT NULL CHECK (direction IN ('in', 'out', 'system')),
    body         TEXT NOT NULL,
    session_id   UUID,
    duration_ms  INTEGER,
    cost_usd     NUMERIC(10, 4),
    chunk_count  INTEGER,
    metadata     JSONB,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_yindo_log_chat_ts
    ON yindo_telegram_log (chat_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_yindo_log_session
    ON yindo_telegram_log (session_id);
