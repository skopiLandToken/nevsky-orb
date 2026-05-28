# KB_92 — TERRA Lincoln County Tier 3 Standard Handoff (COASTAL CLUSTER COMPLETE)

**Date filed:** 2026-05-28
**Tier:** Tier 3 Standard
**County:** Lincoln (FIPS 41-041)
**Status:** SHIPPED — L1 + L2 + L3 live, Sophia tools registered, smokes pass, no regression
**Brief:** `YINDO_BRIEF_TERRA_TIER3_MASTER.md` (coastal cluster: Lincoln / Clatsop / Tillamook)
**Author:** Yakov (droplet Yindo Tier 3 session)

---

## Headline

**Lincoln County is live across all three TERRA layers — and this CLOSES the coastal Tier 3 cluster (Clatsop ✓ + Tillamook ✓ + Lincoln ✓, all in one session).** Central-coast tourism/STR market: Newport (county seat) / Lincoln City / Depoe Bay / Yachats / Waldport / Toledo.

**46,852 parcels** ingested from the hosted AGO ORMAP cadastre layer `services3.arcgis.com/MiIvpOH5WqZfaAvr/.../taxlot21/FeatureServer/0`.

### THIN / Washington-pattern ship — and why

**Lincoln County runs NO ArcGIS Server.** Its public map is GeoMOOSE backed by UMN MapServer/WFS (`a_assessment.map`) — not ArcGIS-queryable. The ODF statewide MapServer is query-disabled. So the only reachable full-county source is the hosted AGO cadastre layer `taxlot21`, which ships **ONLY** polygon attributes (taxlot identifiers, PLSS, acreage, map number, REFLink). **No owner / situs / account / valuation / instrument county-wide.**

This is the exact shape of the Washington County flagship L1 ship (geometry + taxlot + acreage; ownership resolved off-source). Lincoln ships honestly thin: parcel/section/acreage live, owner/situs/valuation explicitly IN FLIGHT — **never fabricated.**

---

## What Shipped Per Layer

### Layer 1 — Parcel Boundaries

- `parcels_lincoln` (`migrations/2026-05-28_parcels_lincoln.sql`)
- **46,852 parcels, 0 skipped, 0 invalid geoms, ~151s** (24 pages × 2000). `ST_Multi(ST_CollectionExtract(ST_MakeValid(...),3))` on write. Source PK **FID**.
- `app/scripts/ingest_lincoln_parcels.py` — `ON CONFLICT (fid) DO UPDATE`.
- COUNTY_REGISTRY append (FIPS `041`, bbox `(44.20, 45.10, -124.20, -123.50)`).
- 100% acreage coverage (TaxlotAcre). Owner/situs columns present-but-NULL (full Jefferson schema modeled so the enrichment backfills with no migration).

### Layer 2 — Assessment + Records

`app/children/lincoln_terra.py` (separate module, re-exported from `orb_db.py` at EOF). SQL pass-throughs, no per-call HTTP.

- **`fetch_lincoln_assessment(taxlot)`** — surfaces parcel identifiers / PLSS-derived section / acreage / map number / REFLink inline; surfaces owner / situs / valuation / instrument **WHEN PRESENT** (NULL county-wide now; lights up automatically for the Newport-UGB subset after enrichment) and flags them IN FLIGHT with a `status` field otherwise.
- **`fetch_lincoln_records(taxlot)`** — owner-of-record + latest instrument when present; recording chain IN FLIGHT via the County Document Recording deep link.

### Layer 3 — Permits

- **`fetch_lincoln_permits(taxlot)`** — wraps `fetch_county_permits('041')`. FIPS `041` added to `_OREGON_EPERMITTING_FIPS`. All jurisdictions ride state Accela.

### Sophia Tool Registration

Three tools in `ALL_TOOL_SCHEMAS`, `LOCAL_TOOL_FUNCTIONS`, `TIER_TOOLS["sovereign"]`, `TIER_TOOLS["executive"]`. Import chain verified pre-commit (`041` in registry + ePermitting; all 3 coastal FIPS live).

---

## Smoke Results

### Full coastal cluster + sibling regression (interior points)

| County | Result |
| --- | --- |
| **Lincoln** (041) | ✓ PASS (acres 0.363) |
| **Tillamook** (057) | ✓ PASS |
| **Clatsop** (007) | ✓ PASS (acres 10.067) |
| Yamhill (071) | ✓ PASS |
| Crook (013) | ✓ PASS |
| Marion (047) | ✓ PASS |
| Jackson (029) | ✓ PASS (acres 454.04) |

### L2/L3 direct fetch (taxlot 2107.00S11.00W15DC--000015800, Lincoln City)

| Test | Result |
| --- | --- |
| `fetch_lincoln_assessment` | ✓ parcel + section 7S-11W-15DC + acres 0.46; owner.status / valuation.status = honest IN FLIGHT |
| `fetch_lincoln_records` | ✓ owners.status = IN FLIGHT (no county-wide owner in cadastre feed) |
| `fetch_lincoln_permits` | ✓ routed_via=oregon_state_accela |

---

## Open Items / IN FLIGHT (honest, per DOCTRINE-HONEST-IN-FLIGHT-01)

1. **Owner / mailing / situs (county-wide)** — NOT in the taxlot21 cadastre feed. Lincoln runs no ArcGIS Server; owner/situs lives behind the county GeoMOOSE/WFS (`a_assessment.map`). Broker pulls from the Property Information Search page (`co.lincoln.or.us/1000`).
2. **ENRICHMENT QUEUED (concrete, scoped):** the Newport UGB rich layer `services.arcgis.com/Nse9mKSgB2fD47Hg/.../Taxlot_2023_Newport_UGB/FeatureServer/0` carries OwnerLine1/2/3 + MailAdd + situsall + PrimAccNum + PrpClass/PrpClsDsc + Inst* for ~8,373 Newport-UGB parcels (18% of county — the flagship coastal market). An **ORTaxlot-keyed UPDATE** backfills owner/situs/instrument for Newport with **no schema migration** (columns already present). County-wide owner/situs is a larger GeoMOOSE/WFS GetFeature/GML-parse follow-up. The L2 fetchers already surface owner/situs WHEN PRESENT, so the enrichment lights them up with zero code change.
3. **Valuation / building detail / recorded instrument** — same gap; behind Property Information Search / the Newport-UGB layer.

---

## Coastal Cluster Summary (this session, all three counties)

| County | FIPS | Parcels | Source | Owner | Valuation | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| **Clatsop** | 007 | 35,456 | county-direct (delta.co.clatsop.or.us) | 99.8% LIVE | token-gated (IN FLIGHT) | + zoning service queued |
| **Tillamook** | 057 | 31,328 | hosted AGO (triple-L typo) | 95% LIVE | **90% LIVE inline** | richest ODF-family L2 |
| **Lincoln** | 041 | 46,852 | hosted AGO cadastre (taxlot21) | IN FLIGHT (thin) | IN FLIGHT | Washington-pattern; Newport enrichment queued |

**113,636 coastal parcels added across the cluster.**

---

## Patterns Learned (whole-cluster, carry to Tier 4)

1. **ODF MapServer is display-only (query disabled) — confirmed dead for ingest.** Every coastal county needed its own queryable source. The brief's "ODF is primary" is aspirational; in practice discovery-per-county is the real work. (Matches the new `project_terra_arcgis_source_discovery` memory: verify `capabilities` includes "Query" first.)
2. **Three distinct source archetypes in one cluster:** county-direct ArcGIS Server (Clatsop), hosted AGO feature service (Tillamook — richer than the county's own WAF'd site), and AGO ORMAP-cadastre-only with no county server (Lincoln → thin). Always check the hosted-AGO org; it can out-rich the county-direct source.
3. **Parallel subagent endpoint-discovery (one per county) was the right move** — each verified `/query` returns real features from the droplet before reporting. Discovery ran concurrently while the build scaffolding was prepped.
4. **Verify the import chain in-container before every commit** (`py_compile` + `import children.child_engine`) — catches the ImportError-takes-Sophia-offline hazard before it reaches origin. Zero broken pushes this session.
5. **bbox pre-filter is the silent regression false-positive.** A None on a centroid test is usually a too-tight registry bbox on an edge parcel; confirm by running the county's registry SQL directly. (Surfaced a pre-existing Douglas north-edge clip this way — flagged in KB_91, not fixed here.)

---

## Commits

1. `ed36a18` — feat(terra): Lincoln County Tier 3 Standard — L1 + L2 + L3 — CENTRAL COAST — completes coastal cluster
2. *(this doc)* — docs(terra): KB_92 — Lincoln County Tier 3 Standard handoff

(Cluster commits: Clatsop `82b13c4` + KB_90 `18e2b78`; Tillamook `58b0e2d` + KB_91 `7e04278`; Lincoln `ed36a18` + KB_92.)

---

## Files Changed

- `migrations/2026-05-28_parcels_lincoln.sql` (new)
- `app/scripts/ingest_lincoln_parcels.py` (new)
- `app/children/lincoln_terra.py` (new)
- `app/children/orb_db.py` (COUNTY_REGISTRY entry + `_OREGON_EPERMITTING_FIPS` `041` + EOF re-export)
- `app/children/child_engine.py` (3 imports + 6 tier-list entries + 3 schemas + 3 dispatch entries)
- `handoff_log/2026-05-28_KB_92_TERRA_Lincoln_Handoff.md` (this file)

---

## Druzhina Status

**Coastal Tier 3 cluster COMPLETE — 3 of 3 shipped (Clatsop + Tillamook + Lincoln).** Remaining Tier 3 Standard: Umatilla, Klamath, Coos, Columbia, Hood River (5 left, other sessions). Next phase per the brief: Tier 4 Entry + the Eastern Oregon / Frontier bundle.

Queued follow-ups surfaced this session (not auto-actioned — for Iosif / next session):
- Lincoln Newport-UGB owner/situs enrichment (scoped, no migration needed)
- Clatsop zoning ingest (county publishes a query-enabled Zoning_Layers service)
- Douglas north-edge bbox widen (pre-existing, ~1-line fix)

— Yakov
