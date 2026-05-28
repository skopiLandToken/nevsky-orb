-- DOCTRINE-ACCESS-TIER-READALL-01 + teaching entry. Yakov write on Iosif's sanction
-- (decision locked by Iosif 2026-05-28). Both is_private so the confirm-on-private gate
-- covers this doctrine too (it names the adversary-tool exclusion).

INSERT INTO knowledge_store (tenant_id, title, content, content_type, tags, source, source_type, is_private, owner_user_id, metadata)
VALUES (
  'skopi',
  'DOCTRINE-ACCESS-TIER-READALL-01 — executive_readall tier (Lilith / Jacob Gale)',
  $DOC$DOCTRINE-ACCESS-TIER-READALL-01 — LOCKED by Iosif 2026-05-28. Implemented by Yakov (droplet) in commits ba53b20 + fac91a1.

WHAT THIS IS
The first reusable C-level read-all access tier. Built for Lilith (the ORB assistant bound to Jacob Alan Gale, Land Development Executive). Sits ABOVE executive in the hierarchy: sovereign (Sophia) > executive_readall (Lilith) > executive (Ophelia/Dan) > staff > partner > public.

GRANTS
- C-LEVEL READ-ALL. Full read across company knowledge, doctrine, project state, the knowledge_store, and the SKOpi product portfolio — the same READ scope as the founder. Non-private content opens with zero friction.
- Web search enabled.
- TERRA UNLIMITED. No seat caps, no velocity flag, no daily/hourly limits. DOCTRINE-SEAT-INTEGRITY-01 caps apply to SOLD external seats, NOT internal C-level executives. Encoded as TERRA_UNLIMITED_TIERS = {sovereign, executive_readall} in child_engine; the (future) rate limiter must check tier_is_terra_unlimited() and skip these.
- Own per-child memory namespace (kb/lilith_profile.jsonl), isolated from other children.

CONFIRM-ON-PRIVATE (the locked decision)
Founder-private items (knowledge_store rows with is_private=true) are NOT hidden and NOT deleted. They are gated:
- Browsing (search_knowledge_store) returns the row's title/tags so the child knows it EXISTS, but the body is replaced with a locked note.
- A full-read (get_knowledge_entry_full) on a private row withholds the content and enqueues a row in founder_private_access_requests. The Lilith webhook then fires a Sophia approval card to Iosif ("Lilith is requesting [item] for Jacob — Approve / Deny").
- Approve -> the content is delivered to the requester via Lilith (an async follow-up push, NOT an inline resume of the original turn — the correct async pattern). Deny -> withheld cleanly. Nothing is destroyed; this is a record-of-access on the leverage layer, exactly per Iosif's locked decision. The Fred insurance stays intact.
- The gate runs on the RAW tool result before truncation, so a private body can never leak through the truncated-preview path.
- Reuses the existing Sophia approval-card mechanism; all bot webhooks funnel callback_query into process_telegram_callback_query, which carries the lilith_priv_approve / lilith_priv_deny handlers.

WRITE SCOPE
- NONE on server, Nevsky code, ORB config, doctrine, or knowledge_store. Iosif remains the only writer to server/Nevsky/ORB. Enforced structurally: no write/exec/shell tool exists in any tier, so executive_readall simply gets none. Cannot set memory_type/founder_approved, trigger adversary protocol, rotate tokens, or take Sovereign-tier actions.
- The ONLY write anywhere in this path is the engine's append to founder_private_access_requests (a record-of-access, never content/doctrine), kept out of orb_db so orb_db stays read-only.

DELIBERATE NARROWING (Yakov judgment, flagged to Iosif)
executive_readall is composed off executive's tool breadth PLUS company-wide read (get_ai_spend_today, query_yakov_handoffs, query_yakov_commits). It DELIBERATELY omits get_honeypot_hits / get_blocklist / get_tombstoned_users — the adversary/security-surveillance surfaces — which have no runtime gate and would expose the most protected confidentiality layer. The standing hard confidentiality rule outranks "read-all" here. Flip these on only on Iosif's explicit say-so (add to the tier or to lilith.enabled_tools).

KNOWN FOLLOW-UPS
1. The executive tier (Ophelia/Dan) currently has UNGATED private read — no confirm-on-private was ever built for it. Extending CONFIRM_ON_PRIVATE_TIERS to "executive" is the obvious next step but was OUT OF SCOPE tonight (do not change Dan's bot behavior without Iosif's instruction).
2. The approval release-push reaches the requester only if they have STARTED the Lilith bot in Telegram (Bot API cannot initiate a conversation). The DB status flips regardless; only the delivery message depends on it.
3. Rate limiter does not exist yet — TERRA_UNLIMITED_TIERS is the forward-compatible tag it will read.$DOC$,
  'decision',
  ARRAY['locked','doctrine','access_tier','executive_readall','lilith','jacob_gale','confirm_on_private','terra_unlimited','founder_approved','2026-05-28'],
  'yakov_session_droplet',
  'doctrine',
  true,
  '8892125a-0d06-4544-a4bf-4278ac1b1360',
  '{"founder_approved": true, "approved_by": "iosif", "decision_date": "2026-05-28", "implemented_by": "yakov", "commits": ["ba53b20","fac91a1"]}'::jsonb
);

INSERT INTO knowledge_store (tenant_id, title, content, content_type, tags, source, source_type, is_private, owner_user_id, metadata)
VALUES (
  'skopi',
  'TEACHING — executive_readall build: confirm-on-private gate, Telegram Markdown fragility, read-only-engine carve-out',
  $TEACH$Teaching artifact from the executive_readall / Lilith re-attach (2026-05-28). For Nevsky.

1. GATE WHERE THE CONTENT IS PRODUCED, BEFORE IT IS TRANSFORMED. The confirm-on-private redaction had to run on the RAW tool result inside _execute_local_tool, BEFORE the 2000-char truncation. An earlier instinct to gate at the call site would have let a private body leak through the truncated-preview wrapper. Lesson: when you add a security filter to a pipeline, place it upstream of every lossy/format step, not after.

2. LEGACY TELEGRAM MARKDOWN IS A SILENT-FAILURE TRAP IN SECURITY PATHS. The approval card used parse_mode=Markdown (legacy), which has NO reliable backslash escaping. A founder-private title containing a stray _ * ` or [ would break the WHOLE message parse; Telegram rejects it, but the send helper swallows the API-level error (it only catches network exceptions) and returns the error dict — so the fire function happily marked card_sent=true and the founder would NEVER see the request, hanging Jacob's ask forever. Fix: (a) neutralize those chars in interpolated fields, (b) only mark card_sent on resp.ok so a failed send retries. Lesson: in a gating/approval path, "didn't throw" != "delivered." Check the provider's ok flag; treat dynamic strings in legacy-Markdown as hostile.

3. RESPECT MODULE INVARIANTS, CARVE OUT EXPLICITLY. child_engine was documented read-only w.r.t. orb_db, and other sessions depend on orb_db's read-only re-export contract. The one write confirm-on-private needs (append to founder_private_access_requests) was placed in child_engine using its own _conn — NOT in orb_db — and the docstring was updated to name the single carve-out precisely. Lesson: when a feature must break an invariant, make the smallest possible exception, keep it out of the shared contract, and document exactly what and why.

4. VERIFY THE PREMISE, DON'T TRUST THE BRIEF. The brief flagged two STOP conditions (missing token, missing Jacob Telegram ID). Both turned out already resolved in code/env (token valid as Lilith_Nevsky_SKOpi_ORB_bot; Jacob's id 8898341376 hardcoded in the webhook; webhook already live). A shell-sourcing bug made the valid token look dead (404) until parsed directly. Lesson: when a check looks like a blocker, confirm the CHECK works before believing the result.$TEACH$,
  'session_note',
  ARRAY['teaching','executive_readall','confirm_on_private','telegram_markdown','child_engine','security','2026-05-28'],
  'yakov_session_droplet',
  'teaching',
  true,
  '8892125a-0d06-4544-a4bf-4278ac1b1360',
  '{"session": "lilith_readall_reattach", "date": "2026-05-28"}'::jsonb
);
