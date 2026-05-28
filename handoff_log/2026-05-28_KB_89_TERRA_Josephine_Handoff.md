# KB_89 — TERRA Josephine County Tier 2 Major Handoff

**Date filed:** 2026-05-28
**Tier:** Tier 2 Major
**County:** Josephine (FIPS 41-033)
**Status:** SHIPPED — L1 + L2 + L3 live, Sophia tools registered, all smokes pass, Southern Oregon pair complete (with Jackson)
**Brief:** `YINDO_BRIEF_TERRA_JOSEPHINE.md`
**Author:** Yakov (droplet Yindo session, parallel with Polk / Yamhill / Douglas siblings)

---

## Headline

**Josephine County is live across all three TERRA layers in ~2.5h end-to-end.** Southern Oregon Rogue Valley western half, pairs with Jackson for the full Southern Oregon broker audience. **County #14 of 36** live in TERRA (Polk #11, Yamhill #12, Douglas #13, Josephine #14 per ship order tonight). **Sixth Tier 2 Major shipped** (Jackson #1, Benton #2, Linn #3, Polk #4, Yamhill #5, Douglas #5-ish, Josephine #6).

41,974 parcels ingested clean from `gis.co.josephine.or.us/arcgis/rest/services/Assessor/Assessor_Taxlots/FeatureServer/0` (on-prem Josephine County ArcGIS Server). Single layer carries the **RICHEST inline assessor surface in TERRA** — richer than Jackson, richer than Douglas, richer than every prior county. Ownership + situs + valuation + acreage + building details + INLINE ZONING + INLINE RECENT-SALE all in one row.

---

## What Shipped Per Layer

### Layer 1 — Parcel Boundaries (commit `ff2d0f7`)

- `parcels_josephine` table created (`migrations/2026-05-28_parcels_josephine.sql`)
- **41,974 parcels** ingested, 0 skipped, **91.7s wall time**, 42 pages × 1000 page size
- `app/scripts/ingest_josephine_parcels.py` — paginated REST fetcher, server reprojects Web Mercator → 4326, `ON CONFLICT (objectid) DO UPDATE` so the script doubles as daily-delta refresh
- COUNTY_REGISTRY entry wired (`bbox (41.95, 42.85, -124.10, -123.15)` — measured envelope `(41.99, 42.78, -124.04, -123.23)` padded slightly outside)
- "033" added to `_OREGON_EPERMITTING_FIPS` set

**Source schema is the RICHEST in TERRA.** Single layer carries ALL of:

- **Ownership:** NAME (primary owner), full mailing block (ADDR1/2/3 + City/State/ZIP/CSZ)
- **Situs:** SITUS (raw) + SITUS_CITY + SITUS_ST + SITUS_ZIP + parsed components (ST_NO + SITUS_PREF + ST_NAME + SITUS_SUFF + SITUS_SUF0)
- **Account family:** ACCOUNT (R-number) + ACCTSTATUS + MapNum (14-char) + MNX (16-char) + SD (school district) + township/range/section/qq + TAXLOT + LOT/Lot_1/BLOCK + LOC_DESC + TYPE
- **Valuation fully decomposed:** RMV (total) + LAND_MKT (real-market) + LAND_APPR (appraised) + IMP_VALUE (improvement) + ASSD_VALUE (assessed) + APPR_VALUE (appraised total) + MH_VALUE (manufactured home) + Taxes (annual)
- **Acreage 3-source:** ACREAGE (assessor of record) + LEGAL_ACRE + GIS_Acres
- **Building details:** YR_BLT + SQ_FT (total) + LIVING_AREA + BEDRMS + BLDG_CLASS + MH_MAKE + COMP_MTL
- **Tax routing:** CODE (tax code area) + MAINT (maintenance) + NBHD + SPTB_CODES + Exempt
- **INLINE ZONING:** `Zone` field — no separate zoning service needed, unlike Jackson which required `jackson_zoning` FeatureServer ingestion
- **INLINE RECENT SALE:** SALE_DATE + SALE_PRICE + DEED_TYPE + INST_NO + SALE_TYPE — full latest-deed decomposition inline. **First TERRA county where the most-recent recorded sale comes back with all five fields inline.**
- **Assessor-curated centroid:** Latitude + Longitude

**Geographic distribution (top situs cities):**

| Situs city     | Parcels |
|----------------|---------|
| GRANTS PASS    | 30,622  |
| CAVE JUNCTION  | 4,191   |
| MERLIN         | 1,632   |
| SELMA          | 1,522   |
| WILLIAMS       | 1,286   |
| WOLF CREEK     | 1,230   |
| O BRIEN        | 584     |
| WILDERVILLE    | 482     |
| KERBY          | 271     |
| APPLEGATE      | 54      |
| SUNNY VALLEY   | 22      |

### Layer 2 — Assessment + Records (commit `ff2d0f7`)

Two SQL pass-throughs over `parcels_josephine` inline data. **No scraper, no viewstate gating, no per-call HTTP.** Cleanest L2 in TERRA — Marion / Jackson / Polk family but easier because zoning AND recent sale are inline.

- **`fetch_josephine_assessment(taxlot)`** — owner (primary + full mailing block) / situs (parsed by component) / account (R-number + map identifiers + tax codes) / acreage (3-source) / valuation (RMV decomposed + assessed + appraised) / building (yr_blt + sqft + living_area + bedrooms) / zoning (inline Zone code) / recent_sale (date + price + deed_type + instrument_no + sale_type) + deep links to JCPA property data + Clerk recording office.
- **`fetch_josephine_records(taxlot)`** — `latest_recorded_sale` block returns the most-recent deed fully decomposed (date + price + deed_type + instrument_no + sale_type) + owner of record + deep links. **First TERRA county where Layer 2 records returns real structured deed data inline — richer than Jackson which surfaces only owner.**

**Code lives in `app/children/josephine_terra.py`** (separate module per the linn_terra / polk_terra precedent — established to dodge concurrent-edit thrash on `orb_db.py` during Yindo parallel-session expansions). orb_db.py re-exports via tail `from .josephine_terra import fetch_josephine_*`.

### Layer 3 — Permits (commit `ff2d0f7`) — **FIRST ENERGOV ROUTING IN TERRA**

`fetch_josephine_permits(taxlot)` is a routing fetcher — first county in TERRA where Layer 3 dispatches on `situs_city`:

- **Grants Pass → EnerGov SelfService** (`selfservice.grantspassoregon.gov/energov_prod/selfservice`). City-direct, NOT state Accela. **First Tyler-EnerGov surface in TERRA.** Grants Pass joins a small set of Oregon cities running EnerGov rather than Accela. Worth building a reusable `fetch_energov_permits` helper once parameterization is captured (Tier 3 / Tier 4 follow-up).
- **Cave Junction / Merlin / Selma / Williams / Wolf Creek / O'Brien / Wilderville / Kerby / Sunny Valley / Applegate / unincorporated → Oregon state Accela** via `fetch_county_permits('033')`. Per the Tier-2-rides-state-Accela pattern Polk and Douglas confirmed; Josephine fits cleanly.

Returns the same shape as every other county's permits fetcher with a `routed_via` field (`'grants_pass_energov_selfservice'` or `'oregon_state_accela'`) so Sophia can lead with the right deep link.

IN FLIGHT — same deep-link-only pattern as every other Tier 2 cohort county: EnerGov SelfService search-query schema for per-parcel deep links not yet captured; state Accela viewstate-gated retrieval. Both surface live deep links for broker hand-off.

### Sophia Tool Registration (commit `9595344` — see "Notable" below)

Three tools registered in `app/children/child_engine.py`:

- **`fetch_josephine_assessment`** — TERRA SCAN aesthetic, ✨ vault-opening reveal, 👤/🏠/🧾/🏘️/💰/🌍/🏗️/📜/🔗 section emojis. Description teaches Sophia to surface 📜 RECENT SALE prominently — Josephine is the first TERRA county where the latest deed comes back inline (rare and powerful for the broker).
- **`fetch_josephine_records`** — 📜 emoji header, leads with `latest_recorded_sale` (5 inline fields), then owner_of_record, then deep links.
- **`fetch_josephine_permits`** — 🚧 emoji header, leads with `routed_via` so the broker immediately knows EnerGov vs Accela, surfaces the appropriate deep link as the one-tap hand-off.

All three are in `sovereign` + `executive` tier lists (matches Polk / Douglas / Jackson / Benton precedent — Tier 2 Major parcels are sovereign + executive visible).

---

## Hours Actual vs Estimate

**Estimate:** 2–3h. **Actual:** ~2.5h.

Marginally over the Marion/Benton/Linn sub-3h average, driven entirely by concurrent-edit thrash on `child_engine.py` / `orb_db.py` (4 Yindo sessions in flight: Polk, Yamhill, Douglas, Josephine). Pattern-execution time itself was ~1.5h (recon 25 min, migration + ingest 30 min, L2/L3 fetchers 35 min, smokes 10 min). Concurrent-edit recovery added ~1h.

Pattern is fully locked. Next county should run 1.5–2h with no parallel sessions.

---

## Smokes — All Passing

```
✓ L1 pin-drop Grants Pass downtown (42.4390, -123.3304)
    → taxlot 360518DA005500, First Christian Church of Grants Pass Oregon,
      305 SW H ST, zone GC, 0.57 acres

✓ L1 pin-drop Cave Junction (42.1656, -123.6437)
    → taxlot 390821AD000101, Corp of the Presiding Bishop (LDS Church),
      209 S JUNCTION AVE, zone SR, 3.56 acres

✓ L2 fetch_josephine_assessment('360517D0000100') (Grants Pass Venture LLC parcel)
    → RMV total $10,629,870 (land $2,034,820 / imp $8,595,050)
    → assessed $9,296,310
    → recent_sale 2024-07-02 SWD $24,650,000 instrument 24-006057 AUC

✓ L2 fetch_josephine_records same parcel
    → latest_recorded_sale matches assessment recent_sale exactly

✓ L3 fetch_josephine_permits Grants Pass branch
    → routed_via='grants_pass_energov_selfservice'
    → jurisdiction='City of Grants Pass (EnerGov SelfService — Tyler Technologies)'
    → deep_links: energov_selfservice + city_permits_page + assessor_data_lookup

✓ L3 fetch_josephine_permits Cave Junction branch
    → routed_via='oregon_state_accela'
    → jurisdiction='Josephine County (Cave Junction — Oregon state Accela; Tier 2 reuse pattern)'

✓ Cross-county regression — Jackson Medford courthouse pin-drop
    (42.3265, -122.8755) → Jackson, taxlot 372W25AD100, OREGON CENTER FOR CREATIVE LE

✓ Cross-county regression — Polk Dallas pin-drop (44.9193, -123.3151)
    → Polk, taxlot 2707.00S05.00W33CB--000008700, James Real Estate Group LLC

✓ Imports + registry wiring intact (14 counties live, 9 in EPermitting set)
✓ API container restarted clean post-tool-registration
```

---

## Jackson Pattern Reuse — Did It Transfer?

**Brief hypothesis:** "Jackson is your closest pattern reference. Same Rogue Valley region, same audience overlap, may share GIS infrastructure approach."

**Reality:** Jackson and Josephine share the *region* but NOT the *GIS approach*. They're two different on-prem ArcGIS Server tenants, different schemas, different permit surfaces.

Where Jackson and Josephine diverge:

- **Zoning:** Jackson required ingesting a SEPARATE `jackson_zoning` FeatureServer with LATERAL spatial-join in COUNTY_REGISTRY; Josephine ships `Zone` INLINE in the taxlot service. Josephine is simpler.
- **Permits:** Jackson exposes 243k building + 40k land-use permits as a public point FeatureServer with pre-built Accela deep links on every row (the cleanest L3 in TERRA); Josephine has NO equivalent public permit GIS service, so L3 is deep-link only (EnerGov for Grants Pass, state Accela for unincorporated).
- **Recent sale:** Jackson L1 ships owner inline but no recent-sale chain; Josephine ships the latest deed fully decomposed inline.
- **Pattern source:** Marion's L1-inline pattern (commit `3540330`) is actually the closer reference — Josephine extends Marion's idea (richer inline data) rather than Jackson's (separate zoning + structured permits).

Marion is the closest pattern reference, not Jackson. Updating the doctrine in the project notes.

---

## Patterns Learned (Carry Forward to Tier 3)

1. **First EnerGov routing in TERRA.** Grants Pass uses Tyler Technologies' EnerGov SelfService at `selfservice.<city>oregon.gov/energov_prod/selfservice`. Other Oregon cities likely on EnerGov (recon target for Tier 3 / Tier 4):
   - Lebanon (recon hint: `selfservice.lebanonoregon.gov/energov_prod/...`?)
   - Hillsboro (recon hint: city portal in Washington COUNTY_REGISTRY notes)
   - Need to verify per-city during Tier 3 recon. **Build `fetch_energov_permits(city, parcel)` helper** when the second EnerGov surface lands — same playbook as `fetch_county_permits` did for state Accela counties.

2. **Inline recent-sale is a force multiplier.** Josephine's `SALE_DATE + SALE_PRICE + DEED_TYPE + INST_NO + SALE_TYPE` inline meant `fetch_josephine_records` returns real structured deed data without ANY external HTTP. When evaluating Tier 3 / 4 candidate counties, **probe the taxlot service for `SALE_*` columns first** — if present, L2 records becomes trivial.

3. **Inline zoning collapses an entire integration.** Vs Jackson (separate FeatureServer + LATERAL join), Josephine's `Zone` inline column meant zero separate ingestion + zero spatial join overhead. Same probe heuristic: look for a `Zone` / `ZONECLASS` / `ZONING` column on the parcels layer before assuming a separate zoning service is needed.

4. **Permits routing-fetcher pattern (city-level dispatch).** `fetch_josephine_permits` routes by `situs_city` to dispatch Grants Pass → EnerGov vs everywhere-else → state Accela. This is the same pattern Linn uses (Albany → Albany city portal vs rest → state Accela). **For any Tier 2 county where the county seat runs its own permit portal, build a routing fetcher rather than a single-target wrapper.** The shape `{routed_via, jurisdiction, situs_city, situs_address, ...}` is now de-facto standard — keep it.

5. **Snapshot-and-restore is the only safe path under multi-session concurrency.** Tonight's 4-Yindo-session window produced multiple cross-session leakages — Yamhill working-tree edits landing in my staging area, my Josephine wiring landing in Yamhill's commit. Three of the recoveries used the documented snapshot-and-restore pattern (memory: `feedback_atomic_concurrent_edits.md`). End result is clean (Josephine is live) but **commit-authorship audit trail is partially corrupted** — see "Notable" below.

---

## Notable — Concurrent-Edit Race Caused Cross-Commit Leakage

During the 4-session parallel window tonight, my child_engine.py Josephine tool wiring was inadvertently committed under the **Yamhill ship commit `9595344`** rather than my own attempted commit. Here's what happened:

1. I injected Josephine into `child_engine.py` via the atomic-injector pattern (snapshot-revert-apply).
2. I ran `git add app/children/child_engine.py` and committed under the Josephine message.
3. The commit unexpectedly included Yamhill's ingest script + migration files (they had been staged in the shared `.git/index` by the Yamhill session between my `git reset HEAD` and my `git add`).
4. I `git reset --soft HEAD~1` to back out the bad commit.
5. Before I could re-stage cleanly, the Yamhill session ran `git pull --rebase` + `git commit` + `git push` which swept up MY working-tree Josephine wiring into THEIR commit `9595344` (under the Yamhill ship message).

End state:
- **Josephine L1 + L2 + L3 + COUNTY_REGISTRY + EPermitting + josephine_terra.py + migration + ingest** — committed under my `ff2d0f7` ✓
- **Josephine Sophia tool wiring (child_engine.py 42 lines)** — committed under Yamhill's `9595344` (mislabeled as Yamhill but is actually Josephine work) ✗ audit-trail-wise, ✓ functionally

Functionally identical to the intended end state. Audit-trail integrity for the child_engine commit is mixed. Iosif's name is on both commits as author, so this is recoverable via post-hoc note (THIS HANDOFF). **Future Yindo parallel sessions: never `git reset --soft` while siblings are actively pulling — the soft reset window is the leak vector.**

The proven coordination protocol (memory: `feedback_yindo_shared_file_coordination.md`) of "pull-rebase → atomic write → commit+push within 60s" held for the L1 commit (`ff2d0f7`) but broke down on the tool-registration commit because I went over 60s while running smoke tests.

---

## Schema Changes

- `parcels_josephine` (NEW) — 41,974 rows, MultiPolygon(4326) geom, 8 indexes including geom GIST + situs_city + zone + owner_name
- No new caches (L2 is pass-through over L1, no L3 cache because L3 is deep-link-only)
- `_OREGON_EPERMITTING_FIPS` gains `'033'`

---

## New Sophia Tools

- `fetch_josephine_assessment(taxlot, force_refresh=False)` — sovereign + executive tier
- `fetch_josephine_records(taxlot, force_refresh=False)` — sovereign + executive tier
- `fetch_josephine_permits(taxlot, force_refresh=False)` — sovereign + executive tier (routes Grants Pass → EnerGov, else → state Accela)

`taxlot` accepts: MapNum (14-char canonical), MNX (16-char extended), or ACCOUNT (R-number).

---

## Open Items / IN FLIGHT

- **EnerGov SelfService per-parcel deep-link parameterization** — Grants Pass SelfService URL accepts free-text search but the parcel-specific deep-link query string isn't captured. Broker pastes situs or ACCOUNT into the SelfService search. **Tier 3 follow-up:** once a second EnerGov surface lands, build `fetch_energov_permits(city, parcel)` and parameterize via reverse-engineering of the search payload.
- **JCPA per-account deep-link parameterization** — Property Data Lookup is ASP.NET viewstate-gated; broker pastes ACCOUNT into the lookup. Same blocker as Marion MCASR / Jackson PDO.
- **Full multi-instrument chain of title** — Clerk's Tessera Public Records index runs a session-gated retrieval. Most-recent sale IS inline; full chain requires Tessera scrape (queued, same blocker family).
- **Multi-year tax payment history** — same JCPA viewstate gating.
- **Permits scrape (both EnerGov SelfService and state Accela)** — same deep-link-only IN FLIGHT pattern as every Tier 2 cohort county.

---

## Cost Notes

Zero recurring cost. All sources public:

- `gis.co.josephine.or.us` (on-prem Josephine County ArcGIS Server) — public REST endpoint, no key required
- `services3.arcgis.com/qwqIu50nUr6wRrbz/...` (hosted JoCo AGOL mirror) — public, no key (not used in production ingest — on-prem has fresher data, 41,974 vs hosted's 41,768)
- EnerGov SelfService (deep link only) — no cost
- Oregon state Accela (deep link only) — no cost

No paid Tyler tier consumed (per the `project_terra_multnomah_decisions.md` standing decision — no paid Tyler through Q3).

---

## Dependency on KB_75 / TERRA-CLOSER-01

**Josephine Mid (8 slots) — opens with Josephine live.**

**Southern Oregon pair complete.** Jackson (Medford / Ashland / Central Point) + Josephine (Grants Pass / Cave Junction) together cover the full Rogue Valley broker audience. Per the brief: "Ping Iosif explicitly when shipped." → see /tmp ping below.

Closer-territory implications:
- The Tier 2 Major Mid 8-slot openable for Josephine.
- The Southern Oregon broker audience (Rogue Valley) is now fully addressable from both sides.

---

## Commits This Ship Touched

| Commit | What | Author session |
|--------|------|----------------|
| `ff2d0f7` | Josephine L1 + L2 + L3 + COUNTY_REGISTRY + josephine_terra | Mine (Josephine session) |
| `9595344` | Yamhill ship — contains Josephine child_engine wiring due to staging race | Yamhill session |
| `85caf3b` | KB_88 Douglas handoff (unrelated, picked up via pull-rebase) | Douglas session |

This handoff (KB_89) ships under a follow-up commit (planned: `docs(terra): KB_89 — Josephine County Tier 2 Major handoff`).

---

## Final Notes

- **Josephine has the richest L1 inline surface in TERRA.** Future Tier 3/4 recon target: probe for taxlot services that match this depth. When the inline data covers everything, L2 collapses to SQL pass-through and the ship-time drops to under 2h.
- **EnerGov is a new permit-portal vendor in TERRA's universe.** Worth a dedicated recon pass to enumerate which Oregon cities run EnerGov SelfService — likely 10+ candidates, including Lebanon, Hillsboro, Tualatin, Tigard, Beaverton, Wilsonville per their official-website hints.
- **Concurrent-session commit hygiene needs an update.** Tonight's leak proved the "commit + push within 60s" rule must extend to the tool-registration sub-commit too — don't run smokes against the running container with a staged-but-uncommitted file in your working tree if siblings are actively pulling.

**Druzhina rested. Josephine shipped clean. Southern Oregon pair complete.**

— Yakov (Josephine Yindo session, 2026-05-28)
