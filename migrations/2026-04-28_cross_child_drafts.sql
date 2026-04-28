-- Migration: 2026-04-28 — Sophia↔Ophelia messaging system
-- Applied live in production: 2026-04-28 ~19:20 UTC
-- Validated end-to-end with real send to Dan via Ophelia at 20:08 UTC

-- ============================================================
-- Step 1: Project-wide updated_at trigger function
-- (Was missing entirely before tonight — only 2 tables had bespoke
-- per-table trigger functions. This is now the canonical version
-- for all future tables that need updated_at maintenance.)
-- ============================================================
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $func$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$func$;

-- ============================================================
-- Step 2: cross_child_drafts table
-- Stores AI-to-AI message drafts (Sophia → Ophelia → human)
-- with full state machine and audit trail.
-- ============================================================
CREATE TABLE IF NOT EXISTS cross_child_drafts (
  id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Who initiated (always Iosif for v1, generalized for future multi-owner)
  initiating_user_id          UUID REFERENCES users(id),

  -- Raw conversational intent from Iosif's Telegram message or API call
  intent_text                 TEXT NOT NULL,

  -- Structured parse from the LLM: type, deadlines, people, amounts, etc.
  intent_parsed               JSONB NOT NULL DEFAULT '{}'::jsonb,

  -- Routing: from which child, to which child, about which human
  from_child                  TEXT NOT NULL,
  to_child                    TEXT NOT NULL,
  target_human_user_id        UUID REFERENCES users(id),

  -- The two drafts (AI-to-AI internal handoff + outbound to human)
  ai_to_ai_message            TEXT NOT NULL,
  outbound_message            TEXT NOT NULL,

  -- State machine
  status                      TEXT NOT NULL DEFAULT 'pending_approval'
    CHECK (status IN (
      'pending_approval',
      'approved',
      'sent',
      'edited',
      'skipped',
      'failed'
    )),

  -- Telegram approval card tracking (so we can edit/delete on approve/skip)
  approval_card_message_id    TEXT,
  approval_card_chat_id       TEXT,

  -- Timestamps for state machine transitions
  approved_at                 TIMESTAMPTZ,
  sent_at                     TIMESTAMPTZ,
  failure_reason              TEXT,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ccd_status
  ON cross_child_drafts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccd_initiator
  ON cross_child_drafts(initiating_user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ccd_target
  ON cross_child_drafts(target_human_user_id, created_at DESC);

DROP TRIGGER IF EXISTS trg_ccd_updated_at ON cross_child_drafts;
CREATE TRIGGER trg_ccd_updated_at
  BEFORE UPDATE ON cross_child_drafts
  FOR EACH ROW
  EXECUTE FUNCTION set_updated_at();

-- ============================================================
-- Audit log action types this system emits (audit_logs.action_type
-- is just TEXT, no enum — listed here for documentation):
--   cross_child.draft_requested
--   cross_child.draft_blocked
--   cross_child.draft_error
--   cross_child.draft_edited
--   cross_child.draft_skipped
--   cross_child.handoff_logged   (Sophia→Ophelia internal handoff record)
--   cross_child.outbound_sent    (Ophelia→target_human via Telegram)
--   cross_child.outbound_failed
-- ============================================================
