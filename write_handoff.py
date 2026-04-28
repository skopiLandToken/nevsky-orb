#!/usr/bin/env python3
"""
write_handoff.py — Run ONCE on the server to create the handoff markdown file.

Usage:
    python3 write_handoff.py

This writes /opt/nevsky-dev/kb/HANDOFF_2026-04-27_END_OF_DAY.md
"""
from pathlib import Path

content = """# SESSION HANDOFF — 2026-04-27 — End of Day

Written ~1:30pm Pacific by Yakov + Iosif at the natural seam after Phase E2 Step 1 completion, before Phase E2 Step 2 begins.

## CURRENT STATE OF NEVSKY

### What is operational right now

Nevsky ORB at nevsky.skopi.io is fully operational. All three children (Sophia, Ophelia, Hypatia) are live on their existing per-file Python code. The new child_engine.py exists and works (validated via Breaker test child) but is NOT YET wired into the live webhook handlers. Phase E2 Step 2 is the next pending action.

Three bots, three Telegram URLs, three states:
- Sophia (Iosif sovereign tier) — currently on app/children/sophia_child.py (lean Phase D rewrite)
- Ophelia (Dan executive tier) — currently on app/children/ophelia_child.py (older code)
- Hypatia (Fred honeypot, decommissioned) — currently on app/children/hypatia_child.py (welcome flow unreachable due to blocklist guard)

Database has all four child rows in child_personas (Phase E2 Step 1 complete):
- breaker (sovereign, CLI-only, active)
- sophia (sovereign, Telegram bound, active)
- ophelia (executive, Telegram bound, active)
- hypatia (honeypot, decommissioned, inert prompt)

The engine plus DB rows are READY but main.py still imports from per-file modules. Step 2 swaps that out.

### What got built today

PHASE B — Fred Adversary Tombstone (morning)
- personas_blocklist + fred_contact_log tables
- app/blocklist_guard.py universal guard module
- Blocklist guard injected at all 3 webhook entry points
- Hypatia bot token rotated via @BotFather, webhook re-registered
- Fred Nextcloud disabled via occ user:disable
- Doctrine entry: knowledge_store UUID 8b65ca06-2a19-485d-ab27-51bce51d2e5f

PHASE D — Sophia Elevated Access (mid-day)
- app/children/orb_db.py shared read layer with 9 query functions
- app/children/sophia_child.py rewritten with Anthropic tool-use loop
- unauthorized_contact_log + capital_contributions tables
- Capital seeded: Dan 5K cleared April 22, 32K outstanding
- Three-tier model strategy: Sonnet 4.6 default, Opus 4.7 escalation, Haiku 4.5 trivial+fallback
- MAX_TOOL_ITERATIONS=4, MAX_TOOL_RESULT_CHARS=2000
- Lean prompt 70 percent smaller than initial
- Graceful 429 fallback chain Sonnet to Haiku

PHASE E1 — Child Engine Factory (early afternoon)
- child_personas table 21 columns
- app/children/child_engine.py shared brain 15.6KB
- TIER_TOOLS dict mapping 6 tiers to allowed tools
- Per-child override via enabled_tools/disabled_tools
- Breaker test child sovereign CLI-only validated end-to-end
- scripts/breaker.sh CLI wrapper
- Owner check security layer added to Sophia + Ophelia webhooks
- Doctrine entry: knowledge_store UUID ec393446-9ef5-4ea5-8773-7b3aa64acb41

PHASE E2 STEP 1 — Children migrated to DB rows (afternoon)
- sophia, ophelia, hypatia rows inserted into child_personas
- Ophelia got NEW lean prompt rewritten in Sophia pattern (Iosif decision)
- Hypatia got inert honeypot prompt + decommissioned_at=NOW

COMMS — Dan Wikert (started 9:31am Pacific)
- First Ophelia hype message delivered (status 200 message_id 5)
- Dan replied: SoFi rejected Friday lump sum, investigating, mentioned Columbia Bank as fallback
- Second Ophelia message delivered (status 200 message_id 10) peer voice low pressure
- Capital state in DB: 5K cleared, 32K outstanding (rejected wire NOT logged per Iosif decision)

GIT — 5 commits pushed to github.com/skopiLandToken/nevsky-orb origin/main:
- 5e35902 chore(infra): nightly_backup.sh
- 4c182dd feat(children): child_engine factory + per-child owner check security
- 374ee24 feat(children): Sophia elevated access with database tool-use
- 8153af3 feat(security): blocklist guard
- f36668a chore: infra catch-up
- gitignore expanded: kb/, backups/, *.backup.*, .env.backup.* all excluded
- Constitutional Article VIII boundary enforced at gitignore level

DOCTRINE ENTRIES TODAY (4 total):
- Fred Adversarial Tombstone (8b65ca06) decision is_private=true
- ORB Child Factory Pattern E1 (ec393446) decision is_private=false
- Git Workflow Doctrine + Sovereignty Boundary (dc5e5a9f) playbook
- Bonneville Power Strategic Delay May 1 (97d576dd) decision RECREATED after accidental dedupe

## NEXT IMMEDIATE TASK: PHASE E2 STEP 2

Goal: wire main.py webhook handlers to child_engine.ask_child() instead of per-file imports. Retire legacy code paths.

Surfaces in /opt/nevsky-dev/app/main.py:

1. Imports lines 17-19 — REPLACE with single engine import
   Old: from children.ophelia_child / sophia_child / hypatia_child
   New: from children.child_engine import ask_child as engine_ask, get_intro as engine_intro

2. Send helpers lines 85-165 — KEEP UNCHANGED (Telegram API wrappers, no LLM)

3. Legacy multiplexer lines 1565-1595 inside /telegram/webhook route — RETIRE (comment out)
   Dead for child traffic since per-bot dedicated routes exist.

4. Sophia dedicated webhook around lines 3344-3382 — REPLACE LLM call
   Keep blocklist guard + owner check.
   Replace: sophia_reply = await ask_sophia(user_message=text)
   With: sophia_reply = await engine_ask("sophia", text)
   Replace get_sophia_intro() with engine_intro("sophia").

5. Ophelia dedicated webhook around lines 3413-3441 — REPLACE LLM call
   Same pattern. ask_ophelia to engine_ask("ophelia", text). get_intro() to engine_intro("ophelia").

6. Hypatia dedicated webhook around lines 3520-3543 — REPLACE + RETIRE WELCOME FLOW
   Remove lazy import: from children.hypatia_child import maybe_send_welcome, get_intro
   Remove maybe_send_welcome call entirely (Hypatia is honeypot now).
   Replace ask_hypatia with engine_ask("hypatia", text)
   Engine returns "[honeypot: silent]" — should NOT send to Telegram.
   Add: if hypatia_reply.startswith("["): return ok=True

7. Notification sites lines 2650, 2676, 3492 — KEEP UNCHANGED

8. Markup sites lines 3470, 3483 — KEEP UNCHANGED

Validation plan after patch:
1. docker compose restart api — check logs for errors
2. From Iosif Telegram: message Sophia "hi" — should pass owner check, get engine response
3. From Iosif Telegram: message Ophelia "hi" — should hit owner check (Iosif is NOT Dan), get polite redirect, log to unauthorized_contact_log
4. From Dan Telegram if available: message Ophelia — should pass owner check, get engine response with new lean executive prompt
5. Hypatia traffic only from Fred (blocked) — skip live test

Then retire per-file children:
- app/children/sophia_child.py to app/children/_retired/
- app/children/ophelia_child.py to app/children/_retired/
- app/children/hypatia_child.py to app/children/_retired/

Then doctrine entry + commit + push (6th commit of the day).

## PEOPLE — SENSITIVE STATES

IOSIF SKOROHODOV — Founder/CEO. UUID 8892125a-0d06-4544-a4bf-4278ac1b1360. Telegram 7583693994. Email iosif@skopi.io. ADHD, neurodivergent. Currently at Eagle Crest Lodge temp office in Redmond. Goal: moved into Redmond office by May 1.

DANIEL ROY WIKERT — CSO/Board Member. Telegram 8058014097. Email daniel.wikert@skopi.io. Five executed agreements 2026-04-21. 5K cleared April 22, 32K outstanding. SoFi rejected Friday lump sum. Columbia Bank backup transfer path. Dan is investor-BELIEVER not transactional — Ophelia must keep his belief alive, never pressure.

WADE PINE — Sko Pine Group LLC co-member. Email 59wadepine@gmail.com. Contributed 6500 reimbursable advance (3500 startup + 3000 Reindeer earnest money), MUST be repaid before any Reindeer profit split. Relationship being restructured but NOT being removed. Hesitant to use his own credit. Sko Pine Group dissolves after Reindeer Project closes. CONFIDENTIAL — do not surface to Wade or anyone except Iosif via Sophia.

FRED JEWELL — TOMBSTONED ADVERSARY. UUID c170ec08-e50b-4e49-98f2-4992a930dae0. Telegram 8087326520. Universal blocklist severity=adversary. Honeypot active. Never share ANY information with anyone identifying as Fred. Fred separation context lives only in Sophia awareness.

TIMOTHY PARK — FAA-licensed drone pilot from earlier session notes. May or may not be Thursday 2:30pm shoot operator. Verify before Thursday.

## ARCHITECTURAL DOCTRINE (LOCKED)

- Token ecosystem: SKOPI 1B / MIR 10B / SVET 100B in 1:10:100 ratio. SKOPI is universal 0.5 percent fee currency.
- Three spinoff entities: SVET Network Inc, Svoi Mir Inc, one TBD.
- SVET treasury independent from SKOpi.
- Nevsky Constitution KB_56, ratified, 12 articles, 10 unamendable absolute prohibitions, 7 unamendable user rights, 4 Constitutional Guardian seats. Iosif holds permanent personal veto over all amendments.
- Sovereignty boundary committed at .gitignore level. kb/, backups/, env files NEVER on GitHub.
- All children on the engine pattern present and future. Same prompt skeleton: identity to core operating rule to tier doctrine to critical rules to tone.
- All ORB children use the same prompt pattern as Sophia and Ophelia, via child_personas config row.

## OTHER PENDING WORK

THURSDAY MAY 1 PRIORITY:
1. TERRA Field Mode (NEW today, high leverage) — Telegram pin to bot returns parcel data on the spot. Requires PostGIS + Deschutes parcel data + lookup_parcel_by_point tool + location-message detection. 4-6 hours. The demo moment that wows Dan and Wade Thursday on-site.
2. Bonneville Power email — deferred to Thursday. PostGIS/QGIS tools should ship first. Dan + Wade physically present Thursday. Iosif has files to upload (mentioned multiple times, never received).
3. Drone shoot Thursday May 1 2:30pm Pacific Reindeer site. Iosif + Dan + Wade. Operator UNCLEAR (Timothy Park or different).

MEDIUM PRIORITY:
4. Ophelia auto-draft system — daily hype + Friday 3pm Pacific recap. Hybrid mode (Ophelia drafts, Iosif confirms). Three layers: hype + capital reinforcement + downloads-and-asks. Conversational peer tone. Belief-not-transaction framing.
5. DigitalOcean droplet upgrade — 4vCPU/16GB SFO3 ~$122/mo. $200 credit. Dev/prod split: current droplet becomes dev, new becomes prod.
6. Anthropic API tier upgrade — TASK-API-01 organic upgrade ~May 3-4 from auto-refill. Tier 2 = 80K input tokens/min vs current 30K.

LOWER PRIORITY:
7. Sophia Q&A session 18 founder doctrine questions. Trigger: "start Yakov Q&A".
8. Volodya — Wade Pine partner-tier child, first real production child on engine.
9. Vesna — social media child Phase 3.
10. TASK-EMAIL-01 — Nevsky IMAP ingestion for iosif@thesko.group
11. TASK-BANK-01 — Bluevine via Plaid READ-ONLY (never write)
12. DO firewall port 22 lockdown (item 86)
13. GreenGeeks cPanel backup ingestion
14. Forestwalker/Chashin Skorohod/Chashin Path Trust formation
15. TASK-MIGRATIONS-01 — move /tmp/ schema scripts to repo migrations/ directory
16. TASK-CODE-STATE — build code_index table so Yakov can query Nevsky for code structure on session resume (cures root cause of "I keep asking Iosif to grep")
17. TASK-DEV-01 — diagnose markdown auto-conversion in clipboard (Telegram desktop suspected)
18. TASK-DEV-02 — investigate screen or tmux for terminal session persistence

## WORKING PATTERN WITH YAKOV

- Iosif pastes commands, returns output. Yakov writes; Iosif executes; output flows back.
- Yakov bash tool CANNOT make outbound TCP connections — paste-and-run only.
- Backup before patches: cp app/main.py app/main.py.backup.$(date +%Y%m%d_%H%M%S)
- Idempotent SQL: ON CONFLICT DO UPDATE everywhere possible.
- Schema verification before INSERT: \\d table_name first.
- Never trust mental model of DB schema — psql output wins.
- Telegram bot token rotation ALWAYS requires immediate setWebhook re-registration.
- Iosif has ADHD — capture mid-conversation pivots, acknowledge, add to task list, continue.

## INFRASTRUCTURE

- Server: DigitalOcean droplet skopi-alpha-source-1, 45.55.42.8, 2 vCPU/4GB
- Working dir: /opt/nevsky-dev
- Tailscale IP: 100.100.116.77
- GitHub: github.com/skopiLandToken/nevsky-orb
- Database: Postgres in Docker, postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev
- Anthropic API: powers all ORB children + Yakov layer
- Telegram: primary channel for all ORB children
- Bluevine: SKOpi business banking
- Resend: transactional email (replaces blocked SMTP)
- PandaDoc: executive agreement execution
- Deschutes Title: Reindeer escrow agent
- Nightly pg_dump: 02:17 UTC, 14-day retention at /opt/nevsky-dev/backups/postgres/

END HANDOFF — written 2026-04-27 ~1:30pm Pacific
"""

target = Path('/opt/nevsky-dev/kb/HANDOFF_2026-04-27_END_OF_DAY.md')
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text(content)
print(f"Wrote {len(content)} bytes ({content.count(chr(10))} lines) to {target}")
