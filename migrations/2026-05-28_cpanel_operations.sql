-- 2026-05-28_cpanel_operations.sql
-- DOCTRINE-MAILBOX-PROVISIONING-01
--
-- Nevsky control plane for GreenGeeks cPanel mailbox provisioning (UAPI, scoped
-- API token). Two tables:
--
--   cpanel_operations      — append-only audit log. EVERY cPanel call writes one
--                            row (success AND failure). The error trail is the
--                            point: a failed provision/delete must leave a record
--                            just like a successful one.
--
--   cpanel_pending_deletes — approval queue for destructive deletes. delete is the
--                            only gated op (DOCTRINE-MAILBOX-PROVISIONING-01): the
--                            Sophia tool enqueues here instead of deleting, a Sophia
--                            approval card fires to Iosif, and the delete executes
--                            only on his tap. Mirrors the founder_private_access_
--                            requests queue pattern (card_sent decouples the engine
--                            write from the async Telegram send).

CREATE TABLE IF NOT EXISTS cpanel_operations (
  id              BIGSERIAL PRIMARY KEY,
  operation       TEXT NOT NULL,          -- provision|list|rotate|delete|quota
  target_email    TEXT,
  invoked_by      TEXT NOT NULL,          -- 'sophia' / 'iosif' / 'manual' / 'lilith' ...
  success         BOOLEAN NOT NULL,
  cpanel_response JSONB,
  error_message   TEXT,
  occurred_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cpanel_ops_email   ON cpanel_operations(target_email);
CREATE INDEX IF NOT EXISTS idx_cpanel_ops_invoker ON cpanel_operations(invoked_by);

CREATE TABLE IF NOT EXISTS cpanel_pending_deletes (
  id            BIGSERIAL PRIMARY KEY,
  target_email  TEXT NOT NULL,
  requested_by  TEXT NOT NULL DEFAULT 'sophia',
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|denied|executed|failed
  card_sent     BOOLEAN NOT NULL DEFAULT false,   -- false => sweeper still owes Iosif a card
  requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  decided_at    TIMESTAMPTZ,
  decided_by    TEXT,
  error_message TEXT
);
-- Sweeper hot path: pending rows that still need a card.
CREATE INDEX IF NOT EXISTS idx_cpanel_pending_open
  ON cpanel_pending_deletes(status, card_sent)
  WHERE status = 'pending' AND card_sent = false;
