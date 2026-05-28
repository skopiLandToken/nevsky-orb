# KB_91 — TERRA Tillamook County Tier 3 Standard Handoff

**Date filed:** 2026-05-28
**Tier:** Tier 3 Standard
**County:** Tillamook (FIPS 41-057)
**Status:** SHIPPED — L1 + L2 + L3 live, Sophia tools registered, smokes pass, no regression
**Brief:** `YINDO_BRIEF_TERRA_TIER3_MASTER.md` (coastal cluster: Lincoln / Clatsop / Tillamook)
**Author:** Yakov (droplet Yindo Tier 3 session)

---

## Headline

**Tillamook County is live across all three TERRA layers — and it's the richest ODF-family L2 in TERRA because VALUATION SHIPS INLINE AND LIVE.** Coastal Tier 3 cluster #2 of 3 (Clatsop ✓, Tillamook ✓, Lincoln next). North-coast tourism/STR market (Pacific City / Rockaway Beach / Manzanita) layered over the dairy belt.

**31,328 parcels** ingested from the hosted ArcGIS Online feed `services.arcgis.com/uUvqNMGPm7axC2dD/.../Tilllamook_Taxlot_Owners/FeatureServer/0`.

> **The triple-L typo "Tilllamook" is in the real service name — preserved verbatim in the ingest URL. Do not "correct" it.**

Same AGO org (`uUvqNMGPm7axC2dD`) that hosts Yamhill_County_Landowners — a State-of-Oregon-affiliated org. County-domain GIS hosts (`gis.co.tillamook.or.us` etc.) have **no DNS from the droplet**; the county website 403s datacenter IPs; the ODF MapServer has query disabled. The hosted AGO layer is the only reachable canonical source.

---

## What Shipped Per Layer

### Layer 1 — Parcel Boundaries

- `parcels_tillamook` (`migrations/2026-05-28_parcels_tillamook.sql`)
- **31,328 parcels, 1 skipped (null row), 0 invalid geoms, ~34s** (16 pages × 2000). `ST_Multi(ST_CollectionExtract(ST_MakeValid(...),3))` on write.
- `app/scripts/ingest_tillamook_parcels.py` — `ON CONFLICT (fid) DO UPDATE` (source PK is **FID**, not OBJECTID).
- COUNTY_REGISTRY append (FIPS `057`, bbox `(45.00, 45.85, -124.10, -123.20)`; north edge intentionally overlaps Clatsop's south — harmless, ST_Contains decides).

**Schema = full Yamhill/ODF family PLUS the richest extras TERRA has seen on an ODF-family feed:**

| Field group | Coverage / note |
| --- | --- |
| **Valuation (ASSESSVAL + LANDVALUE + IMPVALUE)** | **90% — LIVE, inline, not in flight** |
| Owner (OWNERLINE1/2/3) | 95% |
| Situs (SITEADDNAM + SITEADDCTY) | 63% |
| Account + tax status (PRIMACCNUM / ACCTSTATUS / TAXSTATUS) | inline |
| Tax routing (MA / SA / NH) | inline |
| Instrument snapshot (INSTID / INSTTYPE / INSTYEAR / INSTMONTH) | inline (no price) |

**Geographic coverage (by situs city):**

| Jurisdiction | Parcels |
| --- | --- |
| COUNTY (unincorporated) | 12,438 |
| (no situs / vacant) | 11,573 |
| Rockaway Beach | 2,107 |
| Tillamook (city) | 1,806 |
| Manzanita | 1,541 |
| Bay City | 764 |
| Garibaldi | 608 |
| Wheeler | 267 |
| Nehalem | 222 |

### Layer 2 — Assessment + Records

`app/children/tillamook_terra.py` (separate module, re-exported from `orb_db.py` at EOF). Both fetchers are **SQL pass-throughs over L1 inline — no scraper, no viewstate gating, no per-call HTTP.**

- **`fetch_tillamook_assessment(taxlot)`** — owner stack / mailing / situs / account + tax status / property class / PLSS section / acreage (taxlot + map + geodesic) / recording snapshot **+ LIVE valuation (assessed total + land + improvement)**. This is the headline differentiator vs every other ODF-family county.
- **`fetch_tillamook_records(taxlot)`** — most-recent instrument + owners of record.

### Layer 3 — Permits

- **`fetch_tillamook_permits(taxlot)`** — wraps `fetch_county_permits('057')`. FIPS `057` added to `_OREGON_EPERMITTING_FIPS`. All jurisdictions ride state Accela. No county-direct portal / permits GIS.

### Sophia Tool Registration

Three tools in `ALL_TOOL_SCHEMAS` (TERRA-SCAN formatted, 💰 VALUATION surfaced prominently), `LOCAL_TOOL_FUNCTIONS`, `TIER_TOOLS["sovereign"]`, `TIER_TOOLS["executive"]`. Import chain verified pre-commit (`057` in registry + ePermitting).

---

## Smoke Results

### Pin-drop + cross-county regression (real interior points)

| County | Result |
| --- | --- |
| **Tillamook** (057) | ✓ PASS |
| Clatsop (007) | ✓ PASS |
| Yamhill (071) | ✓ PASS |
| Deschutes (017) | ✓ PASS |
| Crook (013) | ✓ PASS |
| Marion (047) | ✓ PASS |
| Jackson (029) | ✓ PASS |
| Lane (039) | ✓ PASS |
| Benton (003) | ✓ PASS |
| Douglas (019) | ⚠ pre-existing bbox edge (see below) — NOT a Tillamook issue |

### L2/L3 direct fetch (taxlot 2903.00N10.00W29DC--000004200, 445 Ridge Ct, Manzanita)

| Test | Result |
| --- | --- |
| `fetch_tillamook_assessment` | ✓ owner=RIECKE, ROBERT J &, situs=445 Ridge Ct Manzanita, **assessed=$635,900 / land=$165,840 / imp=$470,060**, acres=0.184 |
| `fetch_tillamook_records` | ✓ latest_instrument 2020-3802 (CLERK-BOR, 2020-06) |
| `fetch_tillamook_permits` | ✓ routed_via=oregon_state_accela |

---

## Observed Pre-Existing Issue (NOT introduced here — flagging for a follow-up owner)

**Douglas (019) COUNTY_REGISTRY bbox is too tight on the north edge.** A point-on-surface of a far-north Douglas coastal parcel (Roseburg Resources, ~lat 43.914 near Westlake/US-101) falls north of Douglas's registry `bbox` max_lat `43.85`, so `lookup_parcel_by_point` skips Douglas in the bbox pre-filter and returns None. Running the Douglas registry SQL **directly** with that point returns the correct parcel (zone TR, Roseburg Resources Co) — so the data + query are fine; only the bbox clips far-north coastal parcels. **Same class of issue as the documented Clackamas bbox tightness (KB_87).** Douglas-core parcels (Roseburg, ~lat 43.2) resolve fine. Fix = widen Douglas bbox max_lat to ~43.95. Left for the Douglas owner / a bbox-audit pass; out of scope for the coastal Tier 3 ship.

---

## Open Items / IN FLIGHT (honest, per DOCTRINE-HONEST-IN-FLIGHT-01)

1. **RMV real-market value** — the feed ships **assessed** (ASSESSVAL) + land + improvement inline (surfaced LIVE); a distinct RMV figure is not present. Assessed is the headline number.
2. **Recorded sale price + multi-instrument chain** — only the most-recent instrument is inline (id/type/year/month). Price not in source.
3. **Zoning** — no Tillamook zoning service located/ingested yet (Jackson-pattern follow-up). PRPCLSDSC carries the use class.
4. **Building detail** (year built / sqft / bed / bath) — not in source.
5. **Per-parcel assessor / clerk deep link** — Tillamook county website WAF-blocks datacenter IPs (403 to every path). Verified taxlot_rest REST deep link + county home surfaced; per-parcel portal deep link is broker-navigation until a reachable surface is found.

---

## Patterns Learned (carry to Lincoln + Tier 4)

1. **A hosted AGO org can carry MORE than the county's own server.** Tillamook's AGO feed ships valuation that the county website hides behind a WAF. Always check the hosted-AGO layer's full field list — it may out-rich the county-direct source.
2. **Preserve source typos in URLs.** "Tilllamook" (triple-L) is the real service name; correcting it 404s.
3. **bbox pre-filter is the silent failure mode in regression testing.** A None result on a centroid/point-on-surface test is often a too-tight registry bbox on an edge parcel, not a dispatch break. Confirm by running the county's registry SQL directly before calling it a regression. (Surfaced the pre-existing Douglas north-edge clip this way.)

---

## Commits

1. `58b0e2d` — feat(terra): Tillamook County Tier 3 Standard — L1 + L2 + L3 — NORTH COAST + DAIRY BELT — VALUATION LIVE
2. *(this doc)* — docs(terra): KB_91 — Tillamook County Tier 3 Standard handoff

---

## Files Changed

- `migrations/2026-05-28_parcels_tillamook.sql` (new)
- `app/scripts/ingest_tillamook_parcels.py` (new)
- `app/children/tillamook_terra.py` (new)
- `app/children/orb_db.py` (COUNTY_REGISTRY entry + `_OREGON_EPERMITTING_FIPS` `057` + EOF re-export)
- `app/children/child_engine.py` (3 imports + 6 tier-list entries + 3 schemas + 3 dispatch entries)
- `handoff_log/2026-05-28_KB_91_TERRA_Tillamook_Handoff.md` (this file)

---

## Druzhina Status

Coastal Tier 3 cluster: **2 of 3 shipped** (Clatsop ✓ + Tillamook ✓ — Lincoln next, same session). Tillamook is the first ODF-family county to ship valuation LIVE.

— Yakov
