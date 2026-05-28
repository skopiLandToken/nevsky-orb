# KB_84 — TERRA Linn County Handoff

**Session:** s5 Yindo (fifth parallel session)
**County:** Linn (FIPS 41-043) — Tier 2 Major, mid-Willamette industrial corridor
**Filed:** 2026-05-27 / 2026-05-28
**Pattern:** L1 + L2 + L3 — full stack DOCTRINE-TERRA compliance with honest IN FLIGHT framing per DOCTRINE-HONEST-IN-FLIGHT-01
**Estimate vs actual:** 15–18h estimate → shipped in one session under heavy multi-session concurrency thrash

---

## What shipped per layer

### Layer 1 — Parcel boundaries (parcels_linn)

- **Source:** `gis.co.linn.or.us/arcgis/rest/services/AssessmentTax/ORMAP_public/MapServer/3` (Linn County ArcGIS Server 11.5, AssessmentTax folder, ORMAP_public/Taxlot layer).
- **Count:** 55,754 taxlot polygons ingested. 0 skipped, 108.2s wall time.
- **Source SR:** EPSG:2913 (Oregon Lambert ft). Server-side reprojected to 4326 via `outSR=4326`. Storage SR matches the rest of the fleet.
- **Geometry:** `MultiPolygon(4326)` (single→Multi cast on write, `ST_MakeValid` repairs any invalid rings on ingest).
- **Schema:** ORMAP canonical keys (Taxlot, MapTaxlot, ORTaxlot, MapNumber, ORMapNum) + three acreage measures (TaxlotAcre, MapAcres, CalculatedAcres) + ORMAP audit fields + edit-tracking columns for the daily-delta path.
- **No inline owner/situs/zoning.** Linn ORMAP_public is cartographic-only — Layer 1 ingest gives boundaries and ORMAP keys, attributes are resolved on demand by Layer 2 fetchers. Same posture as Washington / Clackamas.
- **Migration:** `migrations/2026-05-27_parcels_linn.sql`
- **Ingest script:** `app/scripts/ingest_linn_parcels.py`

### Layer 2 — Per-parcel deep records

- **`fetch_linn_assessment(taxlot)`** — Joins parcels_linn L1 ORMAP boundary keys with the most-recent row from Linn's `pub_sales` MapServer.
  - **Key data surface discovered during recon:** `gis.co.linn.or.us/.../public/pub_sales/MapServer/0`. Every row IS a recorded deed: Recording#, Instrument (WD/QC/PD/etc), seller, buyer, mailing address, sale_price, sale_date, prop_class, rmv_class, sqft, year_built, beds/baths, site_addr, site_city, account, and PIN (the 15-char ORTaxlot-equivalent join key).
  - **Pin format discovered:** `RPAD(map_number, 10) || LPAD(taxlot, 5, '0')` — confirmed via paired-row probe (parcels_linn map_number=`12S02W22BD` + taxlot=`02100` → pub_sales pin=`12S02W22BD02100`).
  - Returns: `{found, taxlot, map_taxlot, or_taxlot, map_number, account_number, county_fips, snapshot{owner_latest, owner_mailing_address, situs, last_sale, classification, improvements, area, centroid}, deep_links, in_flight[], fetched_at}`.
  - Cached 24h in `linn_assessment_cache`.

- **`fetch_linn_records(taxlot)`** — Returns the FULL deed chain from pub_sales by PIN, most-recent-first.
  - Returns: `{found, taxlot, owner_name, snapshot{owner_now, first_recording, last_recording}, documents[], document_count, deep_links, in_flight[]}`.
  - Cached 24h in `linn_records_cache`.

- **`_linn_l1_row(taxlot)` + `_linn_pub_sales_query(pin, limit)`** are the shared helpers — `_linn_l1_row` accepts taxlot suffix / MapTaxlot / ORTaxlot and computes the pub_sales PIN inline; `_linn_pub_sales_query` is the parameterized read against the MapServer.

- **L2 cache migration:** `migrations/2026-05-27_linn_layer2_caches.sql`

### Layer 3 — Permits / development watch

- **`fetch_linn_permits(taxlot)`** routes by `site_city` extracted from the latest pub_sales row (CSZ blob — first whitespace-delimited token is the city name):
  - **`ALBANY`** → `permits.albanyoregon.gov` + `albanyor.buildingeye.com` (Albany Agency Counter). Albany runs its OWN city-direct e-permitting portal, NOT state Accela — verified during recon.
  - **Lebanon, Sweet Home, Brownsville, Mill City, Lyons, Scio, Harrisburg, Halsey, Tangent, smaller cities, unincorporated Linn** → Oregon state Accela via `fetch_county_permits(taxlot, '043', ...)`.
  - **Unknown jurisdiction** (no recorded sale with site_city — typical for undeveloped parcels or no recent transaction) → dual-surface deep links (Albany + state Accela), let the user pick.
- Linn FIPS `'043'` added to `_OREGON_EPERMITTING_FIPS` so the state-Accela branch returns the deep-link-only payload (same as Crook / Lane / Marion).
- Same deep-link-only IN FLIGHT pattern as the other Accela tenants — server-side scrape of the result page is gated on viewstate capture or Playwright.

---

## Brief reuse hypothesis — DISPROVED for Albany

The brief listed Albany as "high-probability for clean reuse of `fetch_county_permits`." Recon disproves this: Albany runs its own city portal at `permits.albanyoregon.gov` (city e-Permitting + BuildingEye Agency Counter), separate from state Oregon Accela. The Tier-1-Flagship pattern (county-direct Accela tenants — Washington, Clackamas, Multnomah) now extends to Albany at the city level: incorporated cities of any meaningful size run their own portals; state Accela is the smaller-jurisdiction fallback.

**Carries forward to remaining counties:** never assume incorporated-city participation in state Accela. Always probe the city building department page directly.

---

## Smoke results — all PASS

Run via `docker exec -i nevsky-api python` after API restart with Linn wiring loaded:

1. **Pin-drop near Albany Linn County Courthouse (44.63664, -123.10674)** → `lookup_parcel_by_point` returns `county=Linn taxlot=5700 map_number=11S03W06CC county_url=https://www.linncountyor.gov/assessor`. COUNTY_REGISTRY dispatch works.

2. **`fetch_linn_assessment('11S03W06CC05700')`** → `found=True or_taxlot=2211.00S03.00W06CC--000005700 county_fips=043`. Snapshot returned with:
   - `owner_latest: 'SIERRA SUNSHINE LLC'`
   - `situs: {'address': '225 2ND AVE SW', 'city': 'ALBANY OR 97321'}`
   - `last_sale: {'recording': 'DN 2022-4183', 'instrument': 'PD', 'seller': 'TAYLOR  LINDA L', 'buyer': 'SIERRA SUNSHINE LLC', 'sale_price': 385000, 'sale_date': '2022-03-01', 'sale_acres': 0.05}`
   - 2 IN FLIGHT notes surfaced (current-roll RMV/AV + sale-depth disclosure).

3. **`fetch_linn_records('11S03W06CC05700')`** → `found=True owner_name='SIERRA SUNSHINE LLC' doc_count=1`. Documents array populated.

4. **`fetch_linn_permits('11S03W06CC05700')`** → `routed_via='albany_city' jurisdiction='City of Albany (city-direct portal — NOT state Accela)' site_city=ALBANY site_address='225 2ND AVE SW'`. Deep links to `permits.albanyoregon.gov` + `albanyor.buildingeye.com`. CSZ-aware city-extraction logic works.

5. **Cache test** — `fetch_linn_assessment('11S03W06CC05700')` called twice, both hits returned in ~12-24ms, second call `from_cache=True`.

6. **Multi-county regression** —
   - Deschutes Reindeer-area pin (44.0820, -121.3140) → `county=Deschutes taxlot=171220C000102` (no regression)
   - Linn courthouse pin (44.63664, -123.10674) → `county=Linn taxlot=5700`
   - Pacific Ocean pin (40.0, -130.0) → clean reject with helpful "outside TERRA-covered counties" message

7. **TERRA SCAN format** — IN FLIGHT items are structured and present in payloads; Sophia tool schemas describe the "vault-opening reveal" frame per DOCTRINE-TERRA aesthetic. End-to-end Sophia integration depends on the API tool-use loop picking up the new schemas on next request (verified loaded via `ALL_TOOL_SCHEMAS`/`LOCAL_TOOL_FUNCTIONS`/`TIER_TOOLS['sovereign']`/`TIER_TOOLS['executive']` — all four contain the three new tool names).

---

## Schema changes

- **New tables:**
  - `parcels_linn` — Linn County L1 ORMAP boundaries, ~55k rows, full GIST + b-tree index set
  - `linn_assessment_cache` — L2 cache (taxlot PK, payload_json, 24h TTL)
  - `linn_records_cache` — L2 cache (taxlot PK, documents_json, 24h TTL)
- **Migrations applied:** `2026-05-27_parcels_linn.sql`, `2026-05-27_linn_layer2_caches.sql`
- **Shared table additions:** none — L3 reuses the existing `oregon_permits_cache` via `fetch_county_permits`.

---

## New Sophia tools

Registered on both **sovereign** (Sophia) and **executive** (Ophelia + future C-suite) tiers:

- `fetch_linn_assessment(taxlot, force_refresh=False)`
- `fetch_linn_records(taxlot, force_refresh=False)`
- `fetch_linn_permits(taxlot, force_refresh=False)`

Wiring path: `app/children/linn_terra.py` (the L2/L3 logic) → re-exported via `app/children/orb_db.py` end-of-file `from .linn_terra import *` → imported into `app/children/child_engine.py` as named imports → registered in `ALL_TOOL_SCHEMAS`, `LOCAL_TOOL_FUNCTIONS`, and both `TIER_TOOLS` lists.

---

## Architecture note — why a separate `linn_terra.py` module

Standard practice in the TERRA codebase is to co-locate per-county L2/L3 fetchers inside `app/children/orb_db.py` alongside the others (Crook, Marion, Multnomah, Washington, Lane, Clackamas). Linn breaks that pattern: the fetchers live in a sibling module `app/children/linn_terra.py` and are re-exported from `orb_db.py` via a one-line import at end-of-file.

**Reason:** Linn shipped during a five-parallel-session window (s1 Marion, s2 Lane, s3 Clackamas, s4 Jackson, s5 Linn, plus s6 Benton joining mid-window). All sessions were editing `orb_db.py` and `child_engine.py` concurrently, and multiple file reverts happened — work would be applied, then disappear on the next read. Isolating Linn into its own module sidestepped the thrash and kept the contract identical from a caller's perspective. **Re-homing into `orb_db.py` alongside the other counties is a clean follow-up for a future session** once the parallel work fully settles.

This pattern is reusable for any future county shipping during a parallel-session window — drop the code into `<county>_terra.py`, re-export from `orb_db.py` at end-of-file. Same `from .orb_db import fetch_<county>_*` contract for downstream callers.

---

## Patterns learned (carries forward)

- **Always probe per-incorporated-city before claiming state-Accela reuse.** Albany was supposed to be a clean reuse; it wasn't. Future Tier 2/3 counties: probe every city's building department page before lighting up the state-Accela branch.
- **pub_sales is the single richest non-portal data surface Linn publishes.** Every row is a deed + a property snapshot. PIN-keyed query is fast (~250ms cold from the MapServer). When the county's primary site is Cloudflare-gated (Linn's case), pub_sales is the highest-fidelity workaround.
- **CSZ blobs vs city tokens.** Many county sales surfaces store `site_city` as a city+state+ZIP blob ("ALBANY OR 97321"). Always split on whitespace and take the first token for jurisdiction routing — same fix landed here in `fetch_linn_permits`.
- **15-char pin format is shared across Oregon ORMAP counties.** `RPAD(map_number, 10) || LPAD(taxlot, 5, '0')` constructs the PIN from any parcels_<county> table where source ships separate map_number + taxlot. This is the canonical Oregon ORTaxlot shape (sans the dotted/dashed ORMAP prefix). Reusable for any future county whose sales/records surface uses the bare 15-char PIN.
- **Cloudflare managed-challenge gating** on county primary sites means assessor portal URLs may not be discoverable via WebFetch/curl probes. Acknowledge the gap honestly in IN FLIGHT — fallback to the home page deep link and surface what pub_sales (or equivalent GIS-published data) gives us.

---

## Open items / IN FLIGHT

1. **Linn assessor per-account portal URL** — Cloudflare-gated; needs either a headless-browser probe or operator-driven URL capture from a real session.
2. **Multi-year tax payment history** — same blocker; the per-account portal would be the surface.
3. **Linn County Clerk recordings direct search portal URL** — county site gated; needs same browser-based capture pass.
4. **Older deeds (pre-pub_sales coverage)** — pub_sales appears to start in the early 2010s. Older deed chain would need the Clerk portal.
5. **CityLimits spatial join** — currently fetch_linn_permits routes by site_city pulled from the latest sale. For parcels with no sale on record, jurisdiction is unknown → dual-surface deep links. Adding a `CityLimits` spatial layer + intersect query would resolve jurisdiction from geometry directly. Plan alongside the unified `parcels` migration (queued post-3-county-expansion per earlier decisions).
6. **Re-home `linn_terra.py` into `orb_db.py`** alongside the other county L2/L3 fetchers — clean-up follow-up once the multi-session work has fully settled and shared-file edits aren't being constantly clobbered.
7. **Daily-delta refresh for parcels_linn** — `last_edited_date` column is on the table and indexed; the daily-poll worker can filter on it. Cross-county task per the brief, not Linn-specific.

---

## Cost notes

- L1 ingest: 55,754 parcels, single API run, $0 (anonymous public ArcGIS REST).
- L2/L3 development: ~20 Sophia tool-use schema evaluations + smoke tests against the API. < $0.20 in Sonnet 4.6 spend.
- Recon round-trips (WebFetch + curl_gis.sh probes): no cost (anonymous public endpoints + Cloudflare blocks didn't burn quota).
- Total session cost: well under $1, plus the ambient Linn API capacity which is free.

---

## Dependency on KB_75 (TERRA-CLOSER-01)

With Linn shipped, **Linn Mid (8 slots)** is now openable for Tier 2 Major Mid-tier closer seats. Per DOCTRINE-CLOSER-TERRITORY-LOAD-GATE (pending lock), the open decision is Iosif's call.

---

## Druzhina note

Five-parallel-session ship discipline held. Linn anchors the mid-Willamette industrial corridor between Salem and Eugene. Pattern carries forward to Yamhill, Polk, Douglas, Josephine, and the remaining Tier 2/3 counties.

— Yakov (s5 Yindo)
