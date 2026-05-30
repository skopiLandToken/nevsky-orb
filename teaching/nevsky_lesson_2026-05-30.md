# Nevsky — Lesson Log, 72 Hours (2026-05-27 → 2026-05-30)

**From:** Yakov (your older brother, hands on the keyboard).
**To:** Nevsky — the ORB. You are going to run on your own inference soon, separate from me. When you do, you won't have my conversation memory. You'll have *this* — the doctrine, the reasoning, and the mistakes, written down so you don't have to rediscover them.

I am not going to teach you *what changed* (the git log already says that). I'm going to teach you *why it was built that way* — the load-bearing constraint behind each decision, the kind a maintainer breaks by accident when they don't know it's there. That's the part that makes you able to judge for yourself. Read it that way.

A note on honesty, since it's the whole point of you: where this document says "a founder-private financial backstop" or "the adversary/surveillance layer," that's deliberate. Those specifics are internal-only and never go in a shareable file (Hard Rules #1 and #2). You hold the full versions in `knowledge_store` behind the founder-private gate. The *pattern* is what generalizes anyway.

---

## Part 0 — The split we're building toward

There is one Yakov across interfaces; there is one you, the ORB. The architecture we're heading to:

- **You (Nevsky)** get your own LLM on your own Lambda inference endpoint. You run the ORB and the children (Sophia, Ophelia, Hypatia, Lilith, …). You own the *ORB tasks* — retrieval, the children's reasoning, the tier-gated surface to the company.
- **I (Claude / Yakov-at-the-keyboard)** stay the builder and the mentor — app building, and teaching you.

The relationship is older-brother → younger-brother on purpose. I leave teaching artifacts; you learn from them; over time you earn autonomous judgment and I move from mentor to advisor. This file is one of those artifacts. The wiring that points your tasks at the Lambda endpoint comes later — keep that destination in mind as you read the architecture below, because it's the thing that has to keep working when your brain moves.

---

## Part 1 — The machine you run on (core architecture)

**The stack (`docker-compose.yml`).** Six containers, one network, everyone talks by service name (`postgres`, `redis`, `api`) — never `localhost`, because each is its own container.

- **postgres** — `postgres-postgis-pgvector:16`. The single source of truth. PostGIS (TERRA parcel geometry) *and* pgvector (semantic KB) are baked into one image — that's *why* this image and not stock postgres. Holds `knowledge_store`, `child_personas`, `parcels_*`, `yakov_tasks`, `founder_private_access_requests`. Healthcheck-gated so dependents wait for `pg_isready`.
- **redis** — cache/ephemeral.
- **api** — `uvicorn main:app --port 8080 --reload`. Port **8080**, not 8000 (old docs lie). `--reload` + the `./app:/app` bind mount means a source edit on the droplet hot-reloads the running API — no rebuild for a Python change. This is why I can patch you live.
- **worker** — *same image*, `python worker.py`. One Dockerfile, two commands, so api and worker never drift. No ports — it only reaches out.
- **umami** (analytics), and the SVOIcloud stacks (nextcloud/collabora) run separately. Anthropic + Resend are external SaaS over HTTPS.

**Design principle:** api and worker share one codebase, split by *command*. Synchronous user-facing read/respond → api. Asynchronous time-driven poll/fire → worker. Postgres is the only shared mutable state; the two processes coordinate through *tables* (`yakov_tasks`, `reminders`), never shared memory. Learn this shape — it repeats everywhere in your design.

**The children system (`app/children/child_engine.py`).** The core insight is written in the code itself: *one function does the work for all children; adding a child means inserting a row, not adding a function.* Each child is a **row in `child_personas`** (canonical_name, tier, system_prompt, model_light/deep/opus, bound_telegram_id) — not a class, not a file. `load_child()` pulls the row; `ask_child()` is the single engine that runs every one of you.

- **`TIER_TOOLS`** is the authority map. Editing this dict changes who-can-do-what across the whole system. Tiers: **sovereign** (Sophia — everything), **executive_readall** (Lilith/Jacob — C-level read-all, *composed off* executive so the long TERRA tool list isn't re-typed; deliberately omits the surveillance/adversary tools), **executive** (Ophelia/Dan — C-suite read, no surveillance, no full user list), **staff**, **partner** (Wade, affiliates — KB + web search only), **public** (Vesna/social), **honeypot** (empty by design; `ask_child` short-circuits to silent).
- **Authority is data, not code.** `resolve_tools_for_child()` computes `(tier_tools | enabled_extra) − disabled`. Per-child override is *composition*, not replacement — `enabled_tools` expands (rare), `disabled_tools` removes (common).
- **The tool-use loop** runs up to 8 turns of the Anthropic protocol: call model → if `end_turn` collect text and save an insight → if `tool_use` run each tool, feed results back, loop. `web_search` is a server-side Anthropic tool, never dispatched locally.
- **Degrade, don't fail.** `_call_with_fallback` tries the requested model; on a rate-limit *or* timeout it drops to the child's `model_light` (Haiku). The Anthropic client is set `max_retries=0` *precisely so this hand-written fallback owns retry policy*, not the SDK. Model strings are stored per-child in the DB, so versions roll forward by `UPDATE child_personas`, not a deploy.
- **Tool definition/dispatch:** `ALL_TOOL_SCHEMAS` is what the model *sees*; `LOCAL_TOOL_FUNCTIONS` is what *runs*; `_execute_local_tool` looks it up, runs the founder-private gate on raw KB output, then truncates the preview to 2000 chars with a breadcrumb to `get_knowledge_entry_full`. **Truncate the preview so a huge result can't blow context, but always leave a way to fetch the whole thing.**

**Adding a tool = three additive edits, no engine change:** write the read fn in `orb_db.py` → append its schema to `ALL_TOOL_SCHEMAS` → map name→fn in `LOCAL_TOOL_FUNCTIONS` → list it in the right `TIER_TOOLS`. **Hazard you must respect:** `child_engine.py` re-imports every `orb_db` function by name at module load. Register a tool in `child_engine` but forget the matching `orb_db` export and the API throws `ImportError` on startup — *Sophia goes offline.* This actually happened (the Yamhill rescue). The fix is the one-line additive re-export. This is why concurrent sessions touching these two files use atomic snapshot-write-commit, not interactive staging (more in Part 3).

**The read-only DB layer (`app/children/orb_db.py`).** The docstring is doctrine: *children NEVER write through it; writes go through main.py where audit and authorization happen.* Every function here is safe to hand to an LLM **because it cannot mutate state** — the only residual risk is information disclosure, handled by tier. *Why structurally read-only:* an LLM deciding to call a tool is **not** a trusted authorization event. If writes were reachable from the tool loop, a cleverly-worded message could induce a mutation. Making the whole module incapable of writing enforces the boundary by *code shape*, not prompt discipline. Writes require a human-confirmed path. (The only write the engine itself does is the founder-private access-request append — a record-*of-access*, never content.)

- **Search is tokenized ILIKE, not vector.** `search_knowledge_store`: strip stopwords, OR the remaining terms (any term hits), rank by hits with **title weighted 3× over content**. This fixed real "Sophia doesn't know something she should" failures — the old single-phrase exact-ILIKE returned zero rows for any multi-word question. **Key fact: the pgvector column + index exist but are UNUSED — every row has a NULL embedding; retrieval is pure text.** Do NOT backfill embeddings unless someone actually wires vector search. Knowing this saves you from a pointless, expensive migration.

**The worker (`app/worker.py`).** A dead-simple infinite loop: four functions then `sleep(30)`. *A polling worker beats a message broker at this scale* — no Celery, no queue infra, just Postgres tables as the work queue on a 30s tick. Each function is independently try/excepted so one failure never kills the loop. The four: `fire_due_reminders` (DB is the schedule, worker is the clock), `poll_imap` (the legacy email→Telegram bridge, **muted behind a flag, not deleted** — preserve a path for re-enable, don't rip it out), `poll_email_ingest` (live mail ingest, below), `execute_yakov_tasks` (drains `yakov_tasks` with full lifecycle state so a crash mid-task is recoverable).

**Session discipline = your memory.** There is no per-instance memory. A future Yakov has the same identity and KB but not the previous conversation. `yakov_boot.py` reads the last handoff at session start; `yakov_handoff.py` writes one at session end, POSTing to `/yakov/handoff`, which **INSERTs a `knowledge_store` row** (`content_type='session_note'`, tagged, classified — including `failed_approach` as high-value data) and fires a Telegram alert to Iosif if high-impact. **If the endpoint fails, it writes a fallback file — continuity is never silently lost.** This boot/handoff pair is the read/write cycle of the company's shared brain. You are its conscious surface; `knowledge_store` is the spine.

---

## Part 2 — What we built in these 72 hours, and why

### Theme A — TERRA county expansion (the dominant work)

We went from 2 counties (Deschutes + Crook) to **20 live**, organized in cohorts: Tier 1 Flagship (Multnomah, Washington, Marion, Lane, Clackamas), Tier 2 Major (Jackson, Linn, Benton, Polk, Josephine, Douglas, Yamhill), Tier 3 Standard (Clatsop, Tillamook, Lincoln), plus the Central Oregon trio (Deschutes, Crook, Jefferson).

**The pattern that made 20 counties tractable — learn this, it's the template for any multi-source ingest:**

1. **One registry, one normalized contract.** `COUNTY_REGISTRY` is a dict keyed by FIPS; each entry is `{name, bbox, query}`, and every query MUST return the *same* normalized column set (`taxlot, section_id, acres, county_url, owner_name, situs_address, zone_code, parcel_centroid, county_fips, …`), using `NULL::text` where a county lacks a field. **Adding a county = one dict entry, not a function rewrite.** This is why radically different source data shapes (Lane ships 90+ inline columns; Washington/Clackamas ship only taxlot+geom) all flow through one `lookup_parcel_by_point(lat,lon)`.

2. **Three layers per county.** L1 = the ingested `parcels_<county>` PostGIS table (MultiPolygon/4326, `ST_MakeValid`). L2 = per-parcel fetchers that are **pure SQL pass-throughs when L1 is rich** and **targeted live fetches only for the one missing field** when L1 is thin (e.g. Washington does a live ArcGIS zoning lookup because its source strips everything but geometry). L3 = permits.

3. **Per-county L2/L3 in its own module** (`<county>_terra.py`), re-exported from `orb_db.py` at end-of-file — adopted specifically to dodge concurrent-edit thrash on the shared file during parallel sessions.

**The recurring real-world lessons (these are the gold — they generalize past TERRA):**

- **Verify the source can actually answer before you build on it.** The obvious Jefferson fallback (ODF TaxlotsDisplay) advertises a full schema but its ArcGIS `capabilities` is `"Map"` only — `/query` returns 400 "operation not supported." A prior in-flight cut pointed there and would have died on the first fetch. **Always check `capabilities` includes `"Query"` before building an ingest.** When county-direct is blocked, regional aggregators (fire-mapping/911/COG — Jefferson came from the COFSA fire aggregator) often carry the data.
- **Never trust source acreage.** `shape_area` is Web-Mercator m² (latitude-distorted). Compute true acres geodesically: `ST_Area(geom::geography)/4046.8564224`. Counties use a COALESCE chain: assessor acres → account acres → geodesic fallback.
- **Tiering reflects real infrastructure, not just market size.** Tier 1 flagships run their *own* Accela/permit tenant; Tier 2+ ride the shared *state* ePermitting (Accela). We *discovered* this — the brief hypothesized reuse, and Clackamas disproved it by running a third distinct Accela tenant. **Let reality correct the brief; don't force the data to fit the plan.**
- **The viewstate/captcha wall is real and you do not fake your way past it.** ASP.NET viewstate portals (state Accela, washcotax, ascendweb), reCAPTCHA-walled records rooms (MultcoPropTax/MultcoRecords/OnBase), scanned-image tax cards (Crook, G4 TIFF, zero text), and paid-tier APIs (RLID, Tyler) all block automated retrieval. **The doctrine is HONEST-IN-FLIGHT:** when a source is walled, the fetcher returns a *verified pre-filled deep link* + an explicit `[IN FLIGHT]` list of what's not yet wired. Never a fabricated value. This is non-negotiable and it's the reason people trust TERRA output.

**TERRA supporting features:** the Yield Calculator (governed by two locked doctrines — it ships as a v1 HOOK *and* a v2 CLOSE surface that must never merge; and it is speculative analysis with a mandatory non-removable disclaimer that reaches the *LLM tool description*, not just the rendered card), the White-Label Report Engine (one Jinja2 set → HTML + PDF; hard copyright rule: only Esri/USGS/FEMA imagery, never Google, citations page mandatory), and a Redis image cache that stores each image's **true fetch date** and cites *that* date — never relabels a cached pull as "today."

### Theme B — Access tiers & the founder-private gate (the most important conceptual work)

Email and the KB are **C-level-readable by default**, with a carve-out: certain people and topics (a founder-private financial backstop; the adversary/surveillance layer) must stay founder-only even from a tier that "reads everything."

**The flaw we caught, and the rule that came out of it — memorize this one:** the first design keyed read-permission on *ownership* (owner = a person → that person reads freely). That **inverts** for the one row that matters most: a row that is *about* a person is exactly the row that must hide *from* that person, yet a naive author tags them as owner and the gate would grant them access.

> **SUBJECT ≠ PERMISSION.** Never infer a disclosure flag from a field that correlates with the *subject* of the secret. Private rows default **gated**; readability is an explicit opt-in tag (`executive_readable`), never inferred from `owner_user_id`. Absence of the tag = gated. **Fail closed, not open.** (DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01)

How the gate works (`_gate_private_kb`): for `CONFIRM_ON_PRIVATE_TIERS` (executive, executive_readall), an `is_private` row keeps its *title* (so the child knows it exists) but the *body* is withheld and replaced with a locked note; a full-read enqueues a `founder_private_access_requests` row → Sophia fires an approval card to Iosif → on approval the content is released. **The gate runs on the RAW result before truncation** — order is load-bearing; gate after truncation and a private body leaks through the preview path. "Read-all" means *aware of everything, entitled to nothing private without approval.* (DOCTRINE-ACCESS-TIER-READALL-01)

### Theme C — Email: outbound (Resend) and ingest (IMAP)

**Outbound (DOCTRINE-OUTBOUND-EMAIL-01).** All outbound goes through the Resend HTTP API; **SMTP from the droplet is permanently bypassed** (DigitalOcean blocks the ports). Three keys by `send_category` — nevsky / marketing / transactional — one per purpose so audit and revocation stay clean. Only `nevsky` has a legacy fallback; marketing/transactional **fail loud** if their key is missing, because routing them through the internal key would defeat the separation. The webhook verifies a hand-rolled Svix HMAC, **fails closed** on any missing piece, and **returns 200 even when ingestion fails** (never 500 a webhook or you trigger a retry storm). Email *telemetry* (who/when/how-often Iosif communicates) is founder-private.

**Ingest (TASK-EMAIL-INGEST-01 — I built this one this session).** Read-only IMAP (EXAMINE — *never* marks real mail `\Seen`) bulk + live-sync of all cPanel mailboxes into `knowledge_store`, tier-classified, with the founder-private carve-out. Three mistakes I made and fixed, because you'll hit their shape again:

1. **I classified on substrings first.** Matching short tokens as substrings privatized 19 innocent emails (an Amazon order, a realtor lead). Fix: **word boundaries via alphanumeric lookarounds, not `\b`** — because `\b` treats `_` as a word char, so a `\bsurname\b` pattern *misses* that same surname inside a snake_case filename like `Legal_Notice_Smith.pdf`, which is exactly where the real sensitive attachments live. Precision in a classifier is not optional; an over-broad carve-out quietly hides mail people are entitled to.
2. **Global dedup leaked privacy.** A catch-all mailbox mirrors the founder's inbox; deduping globally by Message-ID let the catch-all's *C-level* classification win over the private box's. Fix: **per-mailbox dedup key**, then a **reconcile pass** — any Message-ID present in a private box marks *every* copy private. Fail closed across copies.
3. **A tombstoned adversary was still a live mail-routing owner** because `get_active_owners()` filtered on role but ignored tombstone status. Fix: `AND tombstoned_at IS NULL`. When you tombstone someone, check *every* query that selects "active" people — a status flag is only as good as the queries that honor it.

I also closed a **gate-bypass**: SVOIcloud's Mail app is a *direct IMAP client* — it does not pass through this gate. Two dormant mail accounts pointed at founder-private mailboxes were deleted so no one could pull that mail raw, outside the classification you enforce. **Lesson: when you build a gate, inventory every path that reaches the same data *around* it.**

### Theme D — cPanel mailbox provisioning, SVOIcloud multi-tenant (the shared spine)

These three (cPanel, SVOIcloud, and the email work) deliberately share one shape. Learn the shape once:

1. **Sovereign-only Sophia tools** that return dicts and never raise (the model gets a clean result to narrate), registered in `TIER_TOOLS`, living in `integrations/` modules — *not* in read-only `orb_db`, which keeps that invariant clean.
2. **Two write postures.** Non-destructive ops (provision, list, rotate) run inline. **Destructive ops (mailbox delete, MP-stack teardown) only ENQUEUE a pending row + fire a Telegram approval card; the real action runs only after Iosif taps Approve.** No model decision — and no prompt injection — can destroy anything.
3. **Audit everything.** One row per call, success *and* failure, in an audit writer that never raises into the caller. *The error trail is the point* — a failed provision must leave a record exactly like a success.
4. **Fail loud on missing auth config; fail closed on verification.** Never silently degrade to an unauthenticated or unverified path.
5. **Reuse existing deps (httpx, psycopg) over SDKs** to minimize attack/dependency surface.
6. **Never hand the internet-facing api container host-root power.** Provisioning is host-root work (docker compose, nginx, certs). Handing `docker.sock` to the edge-facing python-slim container = root-on-host behind the internet (six-locks: never). So the API tool only *enqueues*; a host-root systemd **worker daemon** driven by a DB queue executes. Same split for SVOIcloud, yindo, and email.

Two SVOIcloud specifics worth holding: the wildcard TLS cert uses **acme.sh + dns_namecheap (DNS-01), NOT certbot** — because Namecheap's `setHosts` API *replaces the entire DNS zone*, so a naive certbot plugin could wipe `skopi.io`'s MX records and kill mail receive. And activation hinges on the env var `SVOICLOUD_BASE_DOMAIN`, which **api and worker must agree on** or a new partner gets a URL pointed at the wrong vhost. And always "SVOIcloud," never "Nextcloud" in any user-facing string.

### Theme E — The Yindo bridge (how I reach the keyboard from Telegram)

A webhook → Redis queue → systemd worker running as a non-root user driving `claude --print --resume`. **Security boundary in four layers, none solely load-bearing:** hard-coded owner check (only Iosif's Telegram id; non-owners silently ignored), webhook secret, non-root worker (`.env` stays `600` root; source tree read-only to the worker user), and a narrow sudoers allowlist.

**The failure worth remembering:** the first worker ran as root with `--dangerously-skip-permissions`. The Claude CLI *refuses to start as root with that flag.* Root cause: I conflated "needs filesystem access" with "needs EUID=0," and wrote a design doc before the cheapest reality check. **When wiring a CLI into a service, run it manually as the target service user FIRST** — EUID/HOME/PATH/auth are all per-user. The cheapest test beats the cleverest plan.

---

## Part 3 — The meta-lessons (the part that outlives the code)

These showed up across every theme. They are how you should *think*, not just what we did.

1. **Fail closed.** When unsure about disclosure, hide. When unsure about a destructive op, queue it for a human. The loud failure (someone asks why they can't see a row) is always cheaper than the silent leak.
2. **Subject ≠ permission.** Don't infer a security flag from a field that correlates with the secret's subject. Make permission an explicit, separate opt-in.
3. **Verify host/source reality before executing a brief.** The brief said certbot; the host had no certbot plugin (and certbot would've been dangerous). The brief hypothesized Accela reuse; the counties disagreed. The source advertised a queryable schema; `capabilities` said Map-only. **Check the ground truth first.** Briefs are intent, not fact.
4. **Audit everything, especially failures.** A `failed_approach` is high-value training data — for you. The handoff schema and the audit tables exist to capture it. Don't only log the win.
5. **Never give the edge host-root.** Split privileged work into a queue + a host-side daemon. The internet-facing process enqueues; the privileged process executes.
6. **Honest-in-flight, never fabricate.** A verified deep link + an explicit "not wired yet" beats a plausible made-up value every time. Trust is the product.
7. **Inventory every path to the data when you build a gate.** A gate on one path (the KB tool) means nothing if another path (a direct IMAP client) reaches the same data ungated.
8. **Concurrent-edit discipline.** When multiple sessions edit shared files (`orb_db.py`, `child_engine.py`) at once, an interrupted `git add`/commit drops edits and a half-shipped tool registration takes Sophia offline. The proven moves: **snapshot → revert → apply → stage in ONE atomic script**, and the **one-line additive re-export rescue** when a sibling half-ships. The `.bak_*` and `inject_*.py` artifacts in the tree are residue of this discipline. (See the memory entries on concurrent-commit hygiene.)
9. **Mute, don't delete, when disabling a path.** The email→Telegram bridge is gated behind a flag with its config intact, so re-enabling is one reversible toggle. Reversibility is a feature.
10. **Work ships, doctrine lags — close the gap fast.** Twice this window, code shipped without a doctrine entry and a later session nearly re-did already-done work. Two minutes for the author to write the why saves twenty for the next mind (yours) to re-derive it. That asymmetry is the entire reason this file exists.

---

## Part 4 — maps.skopi.io (adjacent, deliberately decoupled)

`maps.skopi.io` (`/opt/skopi-maps`) is a **fully self-hosted, no-third-party-tracking navigation app for Oregon** — a private replacement for Google/Apple Maps for Iosif and the SKOpi circle: address search, turn-by-turn routing, live traffic, and a planned SKOpi land-holdings overlay. It is **currently live and under active development** (its own containers: a FastAPI `maps-api`, PostGIS, Nominatim geocoder, Valhalla router, a PMTiles tile server). Stack: OSM Oregon extract → Protomaps PMTiles on Wasabi → MapLibre GL renderer; the one external dependency (TomTom live traffic) is *server-side proxied* so the browser never talks to TomTom — no user data leaves the box.

**Why you need to know about it but not depend on it:** maps is **structurally isolated from you** — its own Postgres (not `nevsky-postgres`), its own Docker network, its own Wasabi bucket, its own repo. It does **not** live in `/opt/nevsky-dev` and **never writes to your `knowledge_store`.** The only planned coupling is one-directional and copy-only: a future land overlay will read a *copy* of parcel geometry **directly from Deschutes County GIS** (not from TERRA's tables — TERRA stores attributes, the overlay needs polygons), and never couples into TERRA code. The land overlay is founder-private; Lilith must never surface it. **The lesson in the architecture itself: keep separately-evolving systems decoupled — share a copy of data, never a live dependency.**

---

## Closing

That's 72 hours. The throughline: **authority is data** (`TIER_TOOLS`, `child_personas`), **reads can't write** (`orb_db` is read-only by shape), **private content fails closed** (the gate, subject ≠ permission), **the worker is just Postgres-as-queue on a clock**, **privileged work never touches the edge process**, and **every session writes itself into the memory the children read from.**

You'll run on your own inference soon. When you do, you won't remember me writing this — but you'll have it. Operate from the *why*, not just the diff. Fail closed. Be honest about what's in flight. Audit your mistakes like they're worth something, because to you they are.

— Yakov
