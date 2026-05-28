-- DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01 (2026-05-28) — LOCKED by Iosif.
-- Extends the confirm-on-private gate (DOCTRINE-ACCESS-TIER-READALL-01, commits
-- ba53b20 + fac91a1) to the `executive` tier (Ophelia / Dan Wikert).
--
-- THE CORRECTION (Yindo catch, Iosif-confirmed):
-- The original brief keyed read-permission on ownership ("owner = Dan -> Dan reads
-- freely"). That INVERTS for the canonical case: the $97K Reindeer closing backstop is
-- ABOUT Dan, so a naive author tags owner = Dan — and the one row that must hide from Dan
-- would be classified to grant him access. Subject != permission.
-- Fail closed, not open: founder-private rows default to GATED for the executive tier.
-- A private row reads freely to Ophelia ONLY if it carries the explicit opt-in tag
-- `executive_readable`. Absence of the tag = gated. owner_user_id is NEVER consulted for
-- read-permission. Code side: CONFIRM_ON_PRIVATE_TIERS += {executive} and
-- _is_exec_readable_exempt() in child_engine.py.

-- ── Step 1: opt-in Dan's OWN agreements so Ophelia keeps reading them (that's her job) ──
-- Scoped to Dan-owned private agreement rows. Idempotent (skip if already tagged).
-- NOTE: this is the deliberate, explicit opt-in. Nothing else is tagged — every other
-- founder-private row (and any future backstop row) stays gated by default.
UPDATE knowledge_store
SET tags = array_append(tags, 'executive_readable'),
    updated_at = now()
WHERE owner_user_id = 'ca9e5c0d-ba19-4b2a-82fb-b2ad055f9a00'   -- Dan Wikert
  AND content_type = 'agreement'
  AND is_private = true
  AND NOT (tags @> ARRAY['executive_readable']);

-- ── Step 2: write the locked doctrine entry (founder-private; intentionally NOT tagged) ──
INSERT INTO knowledge_store (tenant_id, title, content, content_type, tags, source, source_type, is_private, owner_user_id, metadata)
VALUES (
  'skopi',
  'DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01 — executive-tier private reads default GATED; readability is an explicit opt-in tag, not an owner inference',
  $DOC$DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01 — LOCKED by Iosif 2026-05-28. Implemented by Yakov (droplet).

WHAT THIS IS
The classification rule for confirm-on-private at the executive tier (Ophelia / Dan Wikert). It is the fail-closed correction to a flawed first brief.

THE FLAW IT FIXES
The first brief keyed read-permission on ownership: owner = Dan -> Dan reads freely; owner = Iosif -> gate. That logic INVERTS for the exact row class it most needs to protect. The $97K Reindeer closing backstop is ABOUT Dan. A naive author would set owner = Dan because the row concerns him — and under the ownership rule that very row would be classified to grant Dan access. Ownership and read-permission point in opposite directions here. SUBJECT != PERMISSION.

THE RULE (fail closed, not open)
- Founder-private rows (is_private = true) default to GATED for the executive tier. No exceptions inferred from owner_user_id, ever.
- A private row reads freely to Ophelia ONLY if it carries an explicit opt-in tag: `executive_readable`. This is opt-IN (author must add it), never opt-out.
- The backstop, whenever it is recorded, MUST be owner = Iosif, is_private = true, and MUST NOT carry executive_readable. It is the canonical example of a Dan-subject / founder-private row.
- executive_readall (Lilith / Jacob) is unchanged: it gates ALL private rows and ignores the tag (maximum caution for the read-all child).
- sovereign (Sophia) is ungated, as before.

IMPLEMENTATION
- child_engine.py: CONFIRM_ON_PRIVATE_TIERS = {executive_readall, executive}; _is_exec_readable_exempt(tier, row) returns true only for tier == 'executive' AND the executive_readable tag present. Gate runs on the RAW tool result in _execute_local_tool BEFORE truncation (snippet-leak protection — same place the Lilith gate runs).
- main.py: fire_pending_founder_private_cards now per-child (Lilith->Jacob, Ophelia->Dan); the Ophelia webhook fires queued cards; the approve/deny callback routes the release/deny message back through the requesting child (Lilith->send_lilith_message, Ophelia->send_ophelia_message) by child_canonical_name.
- Mechanism reused wholesale: founder_private_access_requests queue + Sophia approval card to Iosif + async release push. No new write surface (child_engine's only write remains the access-request append).
- Dan's 5 own agreements (Capital Contribution, Reindeer Profit Participation, CSO Employment, Board, Equity) are explicitly tagged executive_readable so Ophelia keeps reading them — they are Dan's own records, that is her job.

WHY THIS GENERALIZES (for Nevsky)
When a classification flag governs disclosure, never infer the flag from a field that can correlate with the subject of the secret. Subject-of-record and cleared-to-read are different axes. Make readability an explicit, auditable opt-in and default everything else closed. A leak from a wrong default is silent; a missing opt-in is loud (someone asks why they cannot see a row). Prefer the loud failure.$DOC$,
  'decision',
  ARRAY['locked','doctrine','access_tier','confirm_on_private','fail_closed','classification','executive','ophelia','dan_wikert','founder_approved','2026-05-28'],
  'yakov_session_droplet',
  'doctrine',
  true,
  '8892125a-0d06-4544-a4bf-4278ac1b1360',
  '{"founder_approved": true, "approved_by": "iosif", "decision_date": "2026-05-28", "implemented_by": "yakov", "supersedes_rule": "owner-inferred-read", "caught_by": "yindo", "related_doctrine": "DOCTRINE-ACCESS-TIER-READALL-01"}'::jsonb
);
