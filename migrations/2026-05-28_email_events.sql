-- 2026-05-28_email_events.sql
-- DOCTRINE-OUTBOUND-EMAIL-01: raw Resend webhook event capture.
--
-- Why a dedicated raw table (not straight-to-knowledge_store): webhook events
-- arrive one-per-lifecycle-stage (sent -> delivered -> opened -> clicked ...),
-- out of order, and sometimes duplicated (Svix retries on any non-2xx). We keep
-- every raw event here, append-only, and DERIVE one aggregated knowledge_store
-- row per message_id from this table. Raw stays cheap + queryable; KS stays the
-- single founder-private analytics surface. Correlation key is resend_message_id.

CREATE TABLE IF NOT EXISTS email_events (
  id                BIGSERIAL PRIMARY KEY,
  resend_message_id TEXT NOT NULL,
  event_type        TEXT NOT NULL,
  event_data        JSONB NOT NULL,
  send_category     TEXT,
  draft_id          TEXT,            -- our internal email_drafts id, if the send was tagged
  recipient_email   TEXT,
  occurred_at       TIMESTAMPTZ NOT NULL,
  received_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_email_events_msg       ON email_events(resend_message_id);
CREATE INDEX IF NOT EXISTS idx_email_events_draft     ON email_events(draft_id);
CREATE INDEX IF NOT EXISTS idx_email_events_type_time ON email_events(event_type, occurred_at);

-- Idempotency: Svix retries deliver the same (message_id, event_type, occurred_at)
-- triplet. A partial unique index lets ON CONFLICT DO NOTHING drop exact dupes
-- while still allowing many distinct events per message.
CREATE UNIQUE INDEX IF NOT EXISTS uq_email_events_dedupe
  ON email_events(resend_message_id, event_type, occurred_at);
