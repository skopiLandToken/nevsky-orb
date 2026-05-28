# KB_90 — TERRA Clatsop County Tier 3 Standard Handoff

**Date filed:** 2026-05-28
**Tier:** Tier 3 Standard
**County:** Clatsop (FIPS 41-007)
**Status:** SHIPPED — L1 + L2 + L3 live, Sophia tools registered, smokes pass, no regression
**Brief:** `YINDO_BRIEF_TERRA_TIER3_MASTER.md` (coastal cluster assignment: Lincoln / Clatsop / Tillamook)
**Author:** Yakov (droplet Yindo Tier 3 session)

---

## Headline

**Clatsop County is live across all three TERRA layers.** First county of the coastal Tier 3 cluster (Clatsop / Tillamook / Lincoln). NW corner of Oregon — Astoria / Seaside / Cannon Beach / Gearhart / Warrenton — the coastal tourism + short-term-rental market.

**35,456 parcels** ingested clean from `delta.co.clatsop.or.us/server/rest/services/Taxlots/FeatureServer/1`.

### Critical recon finding — the ODF overlay is a dead end for Tier 3

The Tier 3 brief named the **ODF statewide TaxlotsDisplay MapServer** (`gis.odf.oregon.gov/ags1/.../MapServer/<layer>`) as the primary L1 source. **It does not work: query is DISABLED on that service** (capability `"Map"` only — every `/query` returns `error 400 "Requested operation is not supported by this service"`). This is almost certainly why the parallel Jefferson session's `parcels_jefferson` table is still empty and its ingest/migration sit uncommitted — Jefferson's brief pointed at the same dead endpoint.

**The real pattern (confirmed by every shipped county): use the county-direct queryable ArcGIS endpoint.** Each county is its own integration. Discovery is the bottleneck, not the build. (Useful confirmation: the ODF MapServer's layer list IS readable and is a clean FIPS→layer map — Clatsop=3, Lincoln=20, Tillamook=28 — handy for *labeling*, useless for *querying*.)

---

## What Shipped Per Layer

### Layer 1 — Parcel Boundaries

- `parcels_clatsop` table (`migrations/2026-05-28_parcels_clatsop.sql`)
- **35,456 parcels, 0 skipped, ~113s wall** (8 pages × 5000 maxRecordCount)
- **19 self-intersecting rings repaired** via `ST_MakeValid` — the ingest now wraps geom in `ST_Multi(ST_CollectionExtract(ST_MakeValid(...), 3))` so re-runs stay clean; the existing 19 were repaired in place.
- `app/scripts/ingest_clatsop_parcels.py` — paginated REST fetcher, `ON CONFLICT (objectid) DO UPDATE` doubles as daily-delta refresh.
- COUNTY_REGISTRY one-entry append (FIPS `007`, bbox `(45.70, 46.35, -124.10, -123.30)`). `lookup_parcel_by_point` untouched.

**Schema is Clatsop-native (NOT the ODF/Yamhill family).** Richer than the thin ODF cadastre on ownership:

| Field group | Coverage |
| --- | --- |
| Owner (OWNER_LINE / OWNER_LL_1 / OWNER_LL_2 + IN_CARE_OF) | 99.8% |
| Account (ACCOUNT_ID) | 99.9% |
| Situs (SITUS_ADDR + SITUS_CITY) | 68% |
| Year built | 67% |

Acreage has **no source column** — derived geodesically (`ST_Area(geom::geography)/4046.8564224`).

**Geographic coverage (by situs city):**

| Jurisdiction | Parcels |
| --- | --- |
| (no situs / unincorporated / vacant) | 11,316 |
| Astoria | 8,235 |
| Seaside | 6,312 |
| Warrenton | 3,626 |
| Cannon Beach | 2,157 |
| Gearhart | 1,973 |
| Hammond | 604 |
| Arch Cape | 469 |
| Westport | 403 |

### Layer 2 — Assessment + Records

`app/children/clatsop_terra.py` (separate module — linn/polk/yamhill/josephine precedent — re-exported from `orb_db.py` at EOF). Both fetchers are **SQL pass-throughs over L1 inline — no scraper, no viewstate gating, no per-call HTTP.**

- **`fetch_clatsop_assessment(taxlot)`** — owner stack (up to 3 lines + in-care-of) / mailing block / situs / account / property class + stat class / year built / septic status / tax routing (MA / NH / tax_code) / geodesic acreage / deep links.
- **`fetch_clatsop_records(taxlot)`** — owner of record + recording deep links. (Source carries **no** instrument fields → full chain of title is IN FLIGHT.)

### Layer 3 — Permits

- **`fetch_clatsop_permits(taxlot)`** — wraps `fetch_county_permits('007')`. FIPS `007` added to `_OREGON_EPERMITTING_FIPS`.
- All jurisdictions ride state Accela (`aca-oregon.accela.com/oregon`). No county-direct portal; the county `Code_Compliance` MapServer is code enforcement, not building permits. Tier-3-rides-state-Accela pattern holds.

### Sophia Tool Registration

All three tools registered in `ALL_TOOL_SCHEMAS` (TERRA-SCAN formatted), `LOCAL_TOOL_FUNCTIONS` dispatch, `TIER_TOOLS["sovereign"]`, and `TIER_TOOLS["executive"]`. Import chain verified pre-commit (no ImportError; `007` present in both COUNTY_REGISTRY and `_OREGON_EPERMITTING_FIPS`).

---

## Smoke Results

### Pin-drop + cross-county regression (real parcel centroids)

| County | Result | Taxlot |
| --- | --- | --- |
| **Clatsop** (007) | ✓ PASS | 0408.00N09.00W07DB--000000500 |
| Deschutes (017) | ✓ PASS | 141225A002000 |
| Crook (013) | ✓ PASS | 141525B001300 |
| Yamhill (071) | ✓ PASS | 3605.00S04.00W2400--000001000 |

No regression from the COUNTY_REGISTRY append. Coordinate-based landmark pins (Astoria courthouse / Seaside / Cannon Beach) all resolve to Clatsop with correct owner + situs + city.

### L2/L3 direct fetch (taxlot 0408.00N09.00W08CD--000000400, 1132 Exchange St, Astoria)

| Test | Result |
| --- | --- |
| `fetch_clatsop_assessment` | ✓ owner=American Legion Post #12, situs=1132 Exchange St Astoria, year_built=1924, acres=0.114 |
| `fetch_clatsop_records` | ✓ owner_of_record=American Legion Post #12 |
| `fetch_clatsop_permits` | ✓ routed_via=oregon_state_accela, jurisdiction="Clatsop County (...) — Oregon state Accela; Tier 3 reuse pattern" |

---

## Open Items / IN FLIGHT (honest, per DOCTRINE-HONEST-IN-FLIGHT-01)

1. **Valuation (assessed / RMV)** — NOT in the public Taxlots layer. Clatsop valuation lives in the **token-gated `Assessment_Tax` folder** on the same server (no public access). Broker pulls it from Public Property Search (`clatsopcounty.gov/244`) via `account_id`. Never fabricated.
2. **Zoning** — separate **queryable** `Zoning_Layers/MapServer` (layers 0–5: county + Astoria / Cannon Beach / Gearhart / Seaside / Warrenton). A `clatsop_zoning` ingest + LATERAL spatial join is queued (Jackson pattern). This is the cleanest zoning follow-up in TERRA — the service is already query-enabled.
3. **Recorded sale / chain of title** — no instrument fields in source. Surfaced via County Property Records deep link until a structured recording source is wired.
4. **Building detail beyond year built** (sqft / beds / baths) — behind Public Property Search.
5. **Newport-pattern enrichment N/A here** — Clatsop's primary feed is already owner-rich; no secondary source needed (contrast Lincoln, below).

---

## Patterns Learned (carry forward to Tillamook + Lincoln + all Tier 3/4)

1. **ODF MapServer = query-disabled. Do NOT build on it.** Go county-direct. Budget the discovery step explicitly — it's the real work in Tier 3, not the build. (Subagent endpoint-discovery in parallel worked well: one agent per county, each verified the `/query` actually returns features from the droplet before reporting.)
2. **Verify the import chain in-container BEFORE commit+push.** `python -m py_compile` + an actual `import children.child_engine` catches the ImportError-takes-Sophia-offline failure mode (the half-shipped-sibling hazard) before it reaches origin.
3. **Separate `{county}_terra.py` module is the right call during the parallel window.** Only the COUNTY_REGISTRY entry + a one-line EOF re-export + the `_OREGON_EPERMITTING_FIPS` line + the child_engine registrations touch shared files; the whole L2/L3 body is in a file no sibling opens. Minimal collision surface.
4. **Container module root is `children` (cwd `/app`), not `app.children`.** Ingest runs as `docker compose exec -T api python -m scripts.ingest_<county>_parcels`.

---

## Commits

1. `82b13c4` — feat(terra): Clatsop County Tier 3 Standard — L1 + L2 + L3 — NW COAST (Astoria / Seaside / Cannon Beach) LIVE
2. *(this doc)* — docs(terra): KB_90 — Clatsop County Tier 3 Standard handoff

---

## Files Changed

- `migrations/2026-05-28_parcels_clatsop.sql` (new)
- `app/scripts/ingest_clatsop_parcels.py` (new)
- `app/children/clatsop_terra.py` (new)
- `app/children/orb_db.py` (COUNTY_REGISTRY entry + `_OREGON_EPERMITTING_FIPS` `007` + EOF re-export)
- `app/children/child_engine.py` (3 imports + 6 tier-list entries + 3 schemas + 3 dispatch entries)
- `kb/KB_90_TERRA_Clatsop_Handoff_2026-05-28.md` (this file)

---

## Druzhina Status

Coastal Tier 3 cluster: **1 of 3 shipped** (Clatsop ✓ — Tillamook, Lincoln next, same session). Oregon coverage advancing toward ~23/36.

— Yakov
