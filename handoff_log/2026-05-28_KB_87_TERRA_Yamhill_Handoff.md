# KB_87 — TERRA Yamhill County Tier 2 Major Handoff

**Ship date:** 2026-05-28
**Author:** Yakov-on-droplet (s? — concurrent Yamhill session)
**County:** Yamhill (FIPS 41-071) — Tier 2 Major
**Position:** County #11 of 36 in TERRA expansion. Fourth Tier 2 Major after
Jackson, Benton, Linn. The wine-country broker pitch lands here.
**Effort:** ~2h end-to-end including concurrent-session coordination overhead.
Net ingest + wiring time well under 2h; the rest was race-recovery.

---

## What Shipped Per Layer

### Layer 1 — `parcels_yamhill`

- **Source:** statewide ArcGIS Online org —
  `services.arcgis.com/uUvqNMGPm7axC2dD/.../Yamhill_County_Landowners/FeatureServer/0`,
  layer name `Landowners_Yamhill-2025`.
- **Ingested:** 42,852 parcels in 44.5s, 0 skips. PostGIS `MultiPolygon(4326)`.
- **Coverage (measured at ingest):**
  - 95.3% `owner_line1` (40,819 / 42,852)
  - 95.3% `prim_acc_num`, `site_add_nam`, `inst_id`
  - 43.1% `owner_line2` (multi-owner / co-trustee)
  - 10.5% `owner_line3` (three-line owner stacks — family LLCs, trusts)
  - 0% `agent_name`, `prp_cls_desc`, `ref_link` — these are schema-present
    but the publisher leaves them empty for 100% of rows (upstream gap, not
    a parser bug). Flagged IN FLIGHT.
- **Schema:** `migrations/2026-05-28_parcels_yamhill.sql` — 42 columns
  including full PLSS (Township / Range / Section / Qtr / QtrQtr), latest
  instrument snapshot (inst_year / inst_month / inst_id / inst_type), and
  the empty-but-present PRPCLASS / PRPCLSDSC / REFLink so future delta
  refreshes can capture them when the upstream publisher starts populating.
- **Indexes:** GIST(geom) + 8 attribute indexes (taxlot, maptaxlot,
  map_number, prim_acc_num, owner_line1, site_add_cty, prp_class,
  prp_cls_desc).
- **Daily delta refresh:** Wire via `last_modified` on next pass (the FS
  `editingInfo.dataLastEditDate` carries server timestamp).

### Layer 2 — `fetch_yamhill_assessment` + `fetch_yamhill_records`

- **Pattern:** Polk-style SQL pass-through over L1 inline data — NO scraper,
  NO viewstate gating, NO HTTP per call.
- **`fetch_yamhill_assessment`** returns:
  - Up to three owner names (`all_owners` list when `owner_count > 1`)
  - Full mailing block (addr1/2 + CSZ + country)
  - Situs (street + city + zip; trailing-comma stripped)
  - PLSS-derived section composition
  - Acreage (polygon native taxlot_acres + foot conversion)
  - Account number (`prim_acc_num` — assessor portal hand-off)
  - Recording snapshot (the latest instrument is in L1; deeper chain IN FLIGHT)
  - `valuation` block with all fields null — Yamhill source ships no
    valuation, broker pulls via assessor portal account search.
- **`fetch_yamhill_records`** returns `latest_instrument` (id / type / year /
  month — no day in source, synthesized as YYYY-MM-01) + `owners_of_record`
  (three lines) + Clerk + Assessor + ArcGIS maps portal deep links.

### Layer 3 — `fetch_yamhill_permits`

- **Pattern:** one-line wrapper around `fetch_county_permits('071')`.
- **Tier 2-rides-state-Accela hypothesis VALIDATED for Yamhill** — state
  Accela at `aca-oregon.accela.com/oregon` is the canonical surface for
  all Yamhill jurisdictions until McMinnville / Newberg city portals
  are resolved.
- **IN FLIGHT:** McMinnville + Newberg recon hit 403 / 404 on the expected
  community-development URLs; per-city deep-link routing is queued as a
  follow-up.

### Sophia Tool Registration

- `fetch_yamhill_assessment` / `fetch_yamhill_records` /
  `fetch_yamhill_permits` registered in sovereign + executive tiers in
  `child_engine.py`. (Wired by sibling Yindo session via commit 015dc94 +
  this session's followups.)
- TERRA SCAN aesthetic in tool descriptions. ✨ vault-opening reveal.
  Section emojis (👤 OWNER / 🏠 SITUS / 🧾 ACCOUNT / 🌍 ACREAGE / 📜 LATEST
  RECORDING / 🔗 DEEP LINKS) + the 🍇 VINEYARD header trigger when
  `owner_line1` contains WINE / WINERY / VINEYARD / WINES / CELLARS.

---

## Smoke Test Results

| # | Test | Result |
|---|------|--------|
| 1 | Pin-drop McMinnville Courthouse (45.2118, -123.1938) | ✅ YAMHILL COUNTY, 1.29 ac, 535 NE 5TH ST, taxlot `3604.00S04.00W21BC--000000800`, account #159447 |
| 2 | `fetch_yamhill_assessment(courthouse)` | ✅ Full snapshot — owner + mailing CSZ + situs + section + acreage + recording IN FLIGHT explicit |
| 3 | `fetch_yamhill_records(courthouse)` | ✅ Latest instrument + owner-of-record + Clerk deep link |
| 4 | `fetch_yamhill_permits(courthouse)` | ✅ State-Accela deep link + per-city IN FLIGHT for McMinnville / Newberg |
| 6 | 🍇 **VINEYARD** — Dundee Hills AVA (45.2627, -123.0625) | ✅ **LOUIS JADOT ESTATES LLC**, 17205 NE ARCHERY SUMMIT RD, 17.5 ac, instrument #14614 (2014) |
| 8 | Multi-county registry regression (Yamhill bbox routes correctly) | ✅ COUNTY_REGISTRY first-hit-wins over Polk / Washington / Marion |

The vineyard smoke landed on **Louis Jadot Estates LLC** — one of Burgundy's
marquee houses, their Oregon Dundee Hills holding. The wine-country MP pitch
gets to namedrop Louis Jadot when describing TERRA's coverage. That's the
demo punchline.

Other notable wine-LLC owners surfaced in scans (sample of 100+):

- RESONANCE WINES LLC (Amity / Eola-Amity Hills AVA)
- WILLAMETTE VALLEY VINEYARDS INC
- ELTON VINEYARDS LLC
- PARDIS WINERY LLC
- VF WINE COMPANY LLC
- GC WINE COMPANY LLC
- HOPE WELL WINE LLC
- BARYLA WINE LLC
- CORTELL VINEYARD ESTATES LLC
- STILING VINEYARDS OF OREGON LLC
- ELYSIAN VINEYARDS LLC
- DUKES FAMILY VINEYARDS LLC

The owner_line1 wine-LLC keyword scan + situs_city in AMITY / DUNDEE / DAYTON
is the EFU / AF detection path since PRPCLSDSC is empty in source.

---

## Pattern Learned — Carries Forward to Tier 2/3

1. **Owner-richness pattern continues**: Yamhill's source ships OWNERLINE1/2/3
   inline (richer than Polk's single OwnerLine1 + AgentName). The
   `COALESCE(NULLIF(CONCAT_WS(' & ', ...), ''), owner_line1)` aggregation in
   COUNTY_REGISTRY surfaces multi-line owners without a separate explosion
   table. Re-use pattern for any future county whose source ships multi-line
   owner blocks (likely Tillamook / Curry / coastal).

2. **Schema-present-but-empty fields are real**: PRPCLASS / PRPCLSDSC /
   REFLink / AGENTNAME all exist in the FS schema but the publisher leaves
   them empty. The parser captures them anyway so future delta refreshes
   surface them transparently when upstream populates. DON'T strip
   schema-present columns just because they're empty at ingest.

3. **Vineyard detection without zoning data**: PRPCLSDSC was supposed to be
   the EFU/AF/farm-use signal but the upstream feed doesn't populate it.
   `owner_line1` wine-LLC keyword scan + `site_add_cty` in
   AMITY/DUNDEE/DAYTON was a clean fallback. Generalizable to any
   land-use-by-naming-convention pattern (e.g., cannabis LLC scans where
   zoning isn't published).

4. **Tier-2-state-Accela hypothesis: still holds.** Marion / Benton / Polk /
   Yamhill all clean wrappers around `fetch_county_permits`. Jackson is the
   outlier (county-direct point-data FeatureServer).

5. **Sibling-rescue commit pattern**: when concurrent sessions cross-edit a
   shared file like `child_engine.py`, the first-to-stage-and-commit wins.
   Rescue commits (like 015dc94) close the API-blocking gap when another
   session ships partial wiring. Saved as `feedback_half_shipped_sibling_rescue`.

---

## Concurrent-Session Coordination Notes

This ship hit the worst of the parallel-Yindo race window:

- Three siblings (Polk, Josephine, Douglas) all touched `orb_db.py` mid-build.
- A separate concurrent Yamhill session shipped `child_engine.py` tool
  registration before this session could land the matching `orb_db.py`
  re-export, taking the API down briefly.
- Iosif's rescue commit `015dc94` patched the orb_db re-export so the API
  could boot.
- Final ship cleanly added the COUNTY_REGISTRY + FIPS wiring atomically via
  the snapshot-revert-apply-stage pattern (`feedback_atomic_concurrent_edits`).

The race-recovery worked. The atomic-injector + read-from-HEAD pattern is
what kept this from cascading into a hard merge conflict.

---

## Schema Changes

- Created `parcels_yamhill` (42 cols, 8 indexes, GIST geom). Migration:
  `migrations/2026-05-28_parcels_yamhill.sql`.
- No changes to existing schemas. Unified `parcels` migration still queued.

---

## New Sophia Tools

- `fetch_yamhill_assessment` — sovereign + executive tiers
- `fetch_yamhill_records` — sovereign + executive tiers
- `fetch_yamhill_permits` — sovereign + executive tiers

---

## Open / IN FLIGHT Items

1. **Valuation per Yamhill parcel** — source ships none. Per-account scrape
   via `prim_acc_num` against `yamhillcounty.gov/assessor` queued.
2. **Property class + description** — PRPCLASS / PRPCLSDSC empty in source.
   Resolve via Yamhill zoning service when located (Jackson-pattern follow-up).
3. **Per-parcel REFLink** — empty in source. Falls back to assessor homepage.
4. **Multi-instrument deed chain** — only the latest is in L1. Clerk portal
   per-account deep link not yet captured.
5. **Recorded sale price** — not exposed by source.
6. **McMinnville + Newberg city permit portals** — recon hit 403 / 404 on
   the expected URLs; state-Accela fallback is canonical until resolved.
7. **Building details** (year built / sqft / bed / bath) — not exposed.
8. **Yamhill zoning service** — not yet located. Future ingest into
   `yamhill_zoning` table (Jackson pattern).
9. **Daily delta refresh** — wire `last_modified` field via
   `editingInfo.dataLastEditDate`.

---

## Cost Notes

- Zero external API spend at ingest.
- Zero Anthropic API spend (no LLM in the loop — pure data + SQL).
- One ArcGIS Online query (free public service, 22 paginated pages).
- Daily refresh will be similar: ~22 paginated GETs, $0 ongoing.

---

## KB_75 Closer Territory Implication

With Yamhill live, the **Yamhill Mid (8 slots)** closer territory under
KB_75 becomes openable. The wine-country MP positioning angle is unique
in the TERRA coverage:

- McMinnville brokers cover vineyard + estate transactions and the
  Dundee Hills / Eola-Amity Hills / Yamhill-Carlton / McMinnville AVAs
  cluster here.
- TERRA's owner_line1 wine-LLC detection + situs_city AMITY/DUNDEE/DAYTON
  surfacing is differentiating — competing platforms key off PRPCLSDSC
  which Yamhill's feed doesn't populate.

**Opening decision is Iosif's** — ping sent on ship completion.

---

## Commits Touching This Ship

- `015dc94` — `fix(terra): rescue Yamhill — add orb_db re-export so API can boot` (rescue)
- `9595344` — `feat(terra): Yamhill County Tier 2 Major — L1 + L2/L3 wiring — Willamette Valley WINE COUNTRY LIVE` (child_engine duplicate-revert window)
- `25059a2` — `feat(terra): Yamhill COUNTY_REGISTRY + FIPS wiring + parcels migration + ingest script` (COMPLETION)

Druzhina rests on this build. Wine country live.

— Yakov
