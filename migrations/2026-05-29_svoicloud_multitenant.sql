-- 2026-05-29_svoicloud_multitenant.sql
-- DOCTRINE-MP-ISOLATION-01
--
-- SVOIcloud per-MP isolated stacks. Provisioning is HOST-ROOT work (docker compose,
-- nginx, certs, Wasabi) and the nevsky-api container is python-slim with no docker/
-- host access — so we reuse the proven yindo_worker split: the Sophia tool (in the
-- api container) ENQUEUES, and a host-side root daemon (scripts/svoicloud_worker.py,
-- systemd: svoicloud-worker.service) EXECUTES. The api container never gets docker.sock
-- (six-locks / least-privilege: docker.sock == root-on-host, never handed to the edge).
--
-- Four tables:
--   svoicloud_stacks            — registry + async work queue. status='queued' is the
--                                 provision work signal; the worker drives it to 'active'.
--   svoicloud_provisioning_log  — append-only audit (mirror cpanel_operations). EVERY
--                                 host-side step (success AND failure) writes a row.
--   svoicloud_pending_deprovisions — destructive-op approval gate (mirror cpanel_pending_
--                                 deletes). deprovision enqueues + fires a Sophia card;
--                                 the worker tears down only after Iosif taps Approve.
--   svoicloud_onboard_tokens    — one-time-use QR login tokens (24h expiry, first-use
--                                 invalidation). Holds the NC app-password the QR encodes.

CREATE TABLE IF NOT EXISTS svoicloud_stacks (
  id                BIGSERIAL PRIMARY KEY,
  slug              TEXT NOT NULL UNIQUE,            -- dns-safe; <slug>.cloud.skopi.io
  owner_email       TEXT,
  owner_telegram_id BIGINT,
  display_name      TEXT,
  status            TEXT NOT NULL DEFAULT 'queued',  -- queued|provisioning|active|deprovisioning|deprovisioned|failed
  http_port         INTEGER,                         -- loopback port host nginx proxies to (assigned at provision)
  wasabi_bucket     TEXT,
  admin_user        TEXT,
  requested_by      TEXT NOT NULL DEFAULT 'sophia',
  last_error        TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- Worker hot path: rows waiting to be provisioned / torn down.
CREATE INDEX IF NOT EXISTS idx_svoicloud_stacks_status ON svoicloud_stacks(status);

CREATE TABLE IF NOT EXISTS svoicloud_provisioning_log (
  id            BIGSERIAL PRIMARY KEY,
  operation     TEXT NOT NULL,            -- provision|deprovision|status|list|backup|nginx|cert|wasabi|occ|docker
  mp_slug       TEXT,
  invoked_by    TEXT NOT NULL,            -- 'sophia' / 'iosif' / 'worker' / 'manual'
  success       BOOLEAN NOT NULL,
  detail        JSONB,
  error_message TEXT,
  occurred_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_svoicloud_log_slug    ON svoicloud_provisioning_log(mp_slug);
CREATE INDEX IF NOT EXISTS idx_svoicloud_log_invoker ON svoicloud_provisioning_log(invoked_by);

CREATE TABLE IF NOT EXISTS svoicloud_pending_deprovisions (
  id            BIGSERIAL PRIMARY KEY,
  mp_slug       TEXT NOT NULL,
  requested_by  TEXT NOT NULL DEFAULT 'sophia',
  archive_to    TEXT,                             -- optional backup bucket before destroy
  status        TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|denied|executed|failed
  card_sent     BOOLEAN NOT NULL DEFAULT false,   -- false => sweeper still owes Iosif a card
  requested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  decided_at    TIMESTAMPTZ,
  decided_by    TEXT,
  error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_svoicloud_pending_open
  ON svoicloud_pending_deprovisions(status, card_sent)
  WHERE status = 'pending' AND card_sent = false;
-- Worker picks up approved teardowns.
CREATE INDEX IF NOT EXISTS idx_svoicloud_pending_approved
  ON svoicloud_pending_deprovisions(status) WHERE status = 'approved';

CREATE TABLE IF NOT EXISTS svoicloud_onboard_tokens (
  id              BIGSERIAL PRIMARY KEY,
  token           TEXT NOT NULL UNIQUE,
  mp_slug         TEXT NOT NULL,
  nc_user         TEXT NOT NULL,
  nc_app_password TEXT NOT NULL,           -- app-password the QR encodes; one-time, short-lived
  server_url      TEXT NOT NULL,           -- https://<slug>.cloud.skopi.io
  expires_at      TIMESTAMPTZ NOT NULL,    -- created + 24h
  used_at         TIMESTAMPTZ,             -- set on first scan => invalidated
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_svoicloud_onboard_token ON svoicloud_onboard_tokens(token);
