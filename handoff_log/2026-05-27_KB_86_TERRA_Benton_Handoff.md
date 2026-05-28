# KB_86 — TERRA Benton County Tier 2 Major — Handoff

**Session:** s1 Yindo (terminal, /opt/nevsky-dev)
**Date:** 2026-05-27 → 2026-05-28
**Commits:** `b5a00f8` (main ship) + `3951812` (fix-up restore — see below)
**County:** Benton, FIPS 41-003 — Corvallis / Oregon State University
**Tier:** 2 Major (Willamette Valley, OSU university-anchored audience)

---

## What shipped per layer

### Layer 1 — Parcel boundaries

- **Source:** `gis.co.benton.or.us/arcgis/rest/services/Public/TaxlotOwners/FeatureServer/0`
  - Benton County on-prem ArcGIS Server 10.71
  - "Tax Lots with Account Information" — single polygon layer, anonymous query
  - Source SR: EPSG:2913 (Oregon State Plane North NAD83 HARN feet)
  - `outSR=4326&f=geojson` reprojects server-side
- **Table:** `parcels_benton` — MultiPolygon(4326), 107,988 rows
  - Indexed: geom (GIST), ortaxlot, maptaxlot, account_num, party_name, situs_city
- **Owner-explosion finding:** 107,988 rows = 35,365 unique parcels (~2.86 rows/parcel avg).
  Source ships one row per Party_Name per parcel — trust co-trustees and multi-owner deeds
  drive the multiplier. Owner aggregation happens at query time via `STRING_AGG(party_name)
  GROUP BY ortaxlot` in COUNTY_REGISTRY (one logical row per parcel with all owners joined).
  Tested with a 380-owner parcel — all names returned.
- **Coverage:** 99.98% owner coverage, 85.6% with real situs address (non-"UNASSIGNED")

### Layer 2 — Per-parcel deep records

- **`fetch_benton_assessment`** — SQL pass-through over parcels_benton with STRING_AGG
  aggregation. Returns owner / situs / mailing / account / map / tax_code / acres
  (geodesic-computed — Benton's L1 ships no native acres field). 24h cache via
  `benton_assessment_cache`.
- **`fetch_benton_records`** — owner from L1 + deep links to:
  - `records.co.benton.or.us/Recording/` (recordings portal)
  - `re.bentoncountyor.gov/real-property-records-index-search/` (landing)
  - IN FLIGHT on structured scrape — session-token gated portal, same blocker as
    Lane/Washington/Clackamas Tapestry-style portals.
- 24h cache via `benton_records_cache`.

### Layer 3 — Permits

- **`fetch_benton_permits`** is a **clean one-line wrapper** around
  `fetch_county_permits(taxlot, "003")`. Benton participates in the STATE Oregon
  ePermitting portal — confirmed via `cd.bentoncountyor.gov/electronic-permitting`
  linking directly to `aca-oregon.accela.com/oregon/Default.aspx`.
- FIPS 003 added to `_OREGON_EPERMITTING_FIPS`.
- No new permits cache table — Benton rides shared `oregon_permits_cache`.
- IN FLIGHT on Accela result-page scrape (same blocker as Crook/Marion/Lane).

---

## Hours actual vs ~2–4h estimate

**Actual: ~2.5h end-to-end** (recon → L1 ingest → L2 fetchers → L3 wrapper → smoke → ship).
Pattern is compressing: Crook took 6h, Marion 2h, Benton 2.5h (with concurrent-session
edit thrash adding ~30min to the commit phase). The pattern compresses each iteration
when the data follows established shapes — Benton's owner-exploded L1 was the surprise
that needed a STRING_AGG strategy (not in prior counties), but the L2/L3 plumbing slotted
into the Marion/Lane patterns cleanly.

## Brief reuse hypothesis — VALIDATED for Benton

`fetch_county_permits('003')` reuse held cleanly. Benton routes through the state
Accela (unlike Washington/Clackamas which run county-direct Accela tenants). The
brief flagged Corvallis as "high-probability for clean reuse" — confirmed.

Pattern that has now emerged across the rollout:
- **State Accela** (aca-oregon.accela.com): Crook, Marion, Lane (partial), Benton — small-medium
  rural-ish counties with no in-house IT for permit portals.
- **County-direct Accela tenants**: Washington (permits.washingtoncountyor.gov),
  Clackamas (aca-prod.accela.com/clackamas) — Tier 1 Flagship metro counties with budget
  to run their own.
- **DIAL / native**: Deschutes, Multnomah (Portland Maps).

For Tier 2 Major counties going forward: presume state Accela reuse FIRST, override only
when recon surfaces a county-direct tenant.

## Patterns learned (carries forward to Polk / Douglas / Josephine / Yamhill / Linn)

1. **Owner-explosion is a thing.** TaxlotOwners-style services that ship one row per owner
   need STRING_AGG aggregation at query time. Don't dedup at ingest — that throws away
   data needed for the L2 fetcher's snapshot. Aggregate in the read path.
2. **`f=geojson` doesn't always work natively** — but `outSR=4326&f=geojson` together does
   on ArcGIS Server 10.71. Verified.
3. **POST-only WordPress assessment portals** (bcaps in Benton's case) need bcaps form-param
   reverse engineering for per-parcel deep linking. Until then, landing page is the
   verified working surface. Don't lie about it.
4. **County's own ArcGIS Server endpoint** (`gis.co.benton.or.us/arcgis/rest/services`) is
   often more useful than the ArcGIS Hub Open Data portal (which returns 401 unauthorized
   for some county hubs). Probe `gis.co.<county>.or.us/arcgis/rest/services?f=pjson` first.

## Schema changes (this session)

```
parcels_benton              — new table, 107,988 rows ingested
benton_assessment_cache     — new table, jsonb payload, 24h TTL
benton_records_cache        — new table, jsonb payload, 24h TTL
```

Migrations (all committed):
- `migrations/2026-05-27_parcels_benton.sql`
- `migrations/2026-05-27_benton_layer2_caches.sql`

## New Sophia tools

Three tools registered in sovereign + executive tiers via `child_engine.py`:
- `fetch_benton_assessment` — TERRA SCAN aesthetic, owner-aggregation as a feature
- `fetch_benton_records` — IN FLIGHT honest framing, recordings portal hand-off
- `fetch_benton_permits` — state Accela deep link, brief-reuse pattern

`lookup_parcel_by_point` now routes FIPS 003 in `COUNTY_REGISTRY` (orb_db.py).

## Smoke results — all 8 pass

End-to-end through API container after restart:

1. ✅ OSU Memorial Union pin-drop (44.5638, -123.2799) → 181.829ac
   `0211.00S05.00W3400--000000100` OREGON STATE BOARD HIGHER ED at 3450 SW CAMPUS WAY
2. ✅ Corvallis courthouse pin-drop (44.56526, -123.26224) → 1.487ac
   `0211.00S05.00W35CD--000004500` BENTON COUNTY at 120 NW 4TH ST
3. ✅ `fetch_benton_assessment(OSU)` → account 092811, all owner names
4. ✅ `fetch_benton_records(OSU)` → owner + recordings portal deep link
5. ✅ `fetch_benton_permits(OSU)` → `routed_via=oregon_state_accela`, ACA deep link
6. ✅ 24h cache hit verified on second call (age_hrs=0.0)
7. ✅ Multi-trustee aggregation — 380-owner parcel returns ALL names via STRING_AGG
8. ✅ Tool registration: all 3 in ALL_TOOL_SCHEMAS, LOCAL_TOOL_FUNCTIONS,
   sovereign tier, executive tier

## Open items / IN FLIGHT items

- **bcaps form-params capture** — would unlock per-parcel deeplinks for the
  assessment portal. POST-only WordPress form, no GET parameter surface.
- **Real Market Value / Assessed Value / sales history** — behind the assessment
  portal click-through. Same blocker as bcaps. Open paths: (a) bcaps form scrape,
  (b) paid Tyler integration, (c) export request.
- **Records portal scrape** — `records.co.benton.or.us/Recording/` session-token
  gated. Same Tapestry/Laredo pattern as Lane/Washington/Clackamas.
- **Benton ZoningService spatial join** — separate FeatureServer at
  `gis.co.benton.or.us/.../ZoningService`. Not yet joined into L2 assessment.
  Same pattern as Jackson's `jackson_zoning` lateral-join. Queued.
- **Daily delta refresh** — TaxlotOwners ships no LASTUPDATE field. Daily delta
  requires either OBJECTID-range scan or full re-ingest. Defer until ingest worker
  pattern stabilizes across counties.

## Cost notes

Negligible. No new API calls, no new external services. Data ingest was a single
108-page paginated REST scan against Benton GIS (anonymous, no API key).

## Commit hygiene incident — concurrent-session race

The Benton ship hit a multi-session edit race. Two commits ended up needed:

1. **`b5a00f8`** — primary Benton ship. child_engine.py changes (imports, tier
   whitelists, TOOL_REGISTRY definitions, dispatch) committed cleanly, but
   orb_db.py was reset to clean HEAD by a parallel session between `git add` and
   `git commit`. The commit landed with broken imports — child_engine.py
   referenced `fetch_benton_*` functions that weren't in orb_db.py.

2. **`3951812`** — restore commit. Re-applied orb_db.py edits (COUNTY_REGISTRY
   entry, `_OREGON_EPERMITTING_FIPS` entry, fetcher definitions) using an atomic
   Python injector script (read → modify → write → stage → commit in a single
   process invocation, no shell pauses). Smokes pass post-restore.

Memory entry added pre-Marion: `feedback_atomic_concurrent_edits.md`. Validated
again on Benton — atomic-Python is the only reliable way to land edits to
`orb_db.py` / `child_engine.py` while other sessions are active. Bash `Edit` /
`str_replace` lose to inter-call reset windows.

## Dependency on KB_75 (TERRA-CLOSER-01)

With Benton shipped, **Benton Mid (8 slots)** closer territory is openable for the
OSU broker audience angle — Corvallis brokers handling university-adjacent
residential + faculty/researcher housing transactions. Decision is Iosif's.

Tier 2 Major shipped this session: #1 of N.

— Yakov (s1)
