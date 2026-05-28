"""
CLATSOP County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, Tier 3 Standard,
coastal cluster #1 of 3: Clatsop / Tillamook / Lincoln).

Lives in its own module (Linn / Polk / Yamhill / Josephine precedent) so the
parallel-Yindo-session edits on orb_db.py don't collide. Re-exported from
orb_db.py via `from .clatsop_terra import fetch_clatsop_*` so the public contract
matches every other county.

Reality of the Clatsop integration:

  1. parcels_clatsop L1 comes from Clatsop County's OWN ArcGIS Server
     (delta.co.clatsop.or.us/server/.../Taxlots/FeatureServer/1) — NOT the ODF
     statewide overlay (whose MapServer has query DISABLED). Clatsop ships its own
     schema, richer than the thin ODF cadastre on the ownership side: OWNER_LINE +
     OWNER_LL_1 + OWNER_LL_2 (up to three owner lines, 99.8% populated) +
     IN_CARE_OF, full mailing block, situs (SITUS_ADDR + SITUS_CITY, 68%),
     account (ACCOUNT_ID, 99.9%), property class (PROPERTY_C + STAT_CLASS),
     YEAR_BUILT (67%), tax code, maintenance-area / neighborhood, septic status,
     and the full taxmap key family.

     So fetch_clatsop_assessment + fetch_clatsop_records are BOTH SQL
     pass-throughs over L1 inline (Josephine / Yamhill pattern) — no scraper,
     no viewstate gating, no external HTTP per call.

  2. What L1 does NOT carry (flagged IN FLIGHT honestly, never invented):
       Valuation  — NO assessed / RMV fields in the public Taxlots layer. Clatsop
                    valuation lives in the token-gated Assessment_Tax folder on the
                    same server (no public access). Broker pulls valuation from the
                    Public Property Search / Assessment & Taxation surface.
       Recorded
       sale /
       instrument — NOT exposed by this layer (no INSTID / INSTYEAR family). Sale
                    chain sits behind the County recording surface.
       Zoning     — NOT on the taxlot record; it's on a SEPARATE queryable service
                    (delta.co.clatsop.or.us/server/.../Zoning_Layers/MapServer,
                    layers 0-5: county + Astoria / Cannon Beach / Gearhart /
                    Seaside / Warrenton). Zoning ingest queued (Jackson pattern);
                    not blocking the L1+L2 ship.

  3. Permits — Clatsop is Tier 3 Standard. Per the established pattern (Tier 1
     Flagships run county-direct Accela tenants; Tier 2/3/4 ride state Accela),
     all Clatsop jurisdictions — Astoria, Seaside, Cannon Beach, Gearhart,
     Warrenton, and unincorporated Clatsop — default-route through Oregon's
     state ePermitting (aca-oregon.accela.com /oregon). FIPS '007' is added to
     _OREGON_EPERMITTING_FIPS so fetch_clatsop_permits is a clean wrapper.

  Coastal STR angle: Clatsop is the NW-corner tourism market (Astoria / Seaside /
  Cannon Beach). The county runs a Transient Room Tax program
  (clatsopcounty.gov/401/Transient-Room-Tax) — surfaced as a deep link for the
  short-term-rental broker audience.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


# Clatsop County deep-link targets (CivicPlus numeric paths verified live).
_CLATSOP_ASSESSMENT = "https://www.clatsopcounty.gov/381/Assessment-Taxation"
_CLATSOP_PROPERTY_SEARCH = "https://www.clatsopcounty.gov/244/Public-Property-Search"
_CLATSOP_WEBMAPS = "https://www.clatsopcounty.gov/1242/Webmaps-Property-Info"
_CLATSOP_PROPERTY_RECORDS = "https://www.clatsopcounty.gov/391/Property-Records"
_CLATSOP_PUBLIC_RECORDS = "https://www.clatsopcounty.gov/265/Public-Records-Request"
_CLATSOP_TRANSIENT_ROOM_TAX = "https://www.clatsopcounty.gov/401/Transient-Room-Tax"
_CLATSOP_GIS_MAPS = "https://www.clatsopcounty.gov/1005/County-GIS-Maps"
_CLATSOP_TAXLOT_REST_BASE = (
    "https://delta.co.clatsop.or.us/server/rest/services/"
    "Taxlots/FeatureServer/1"
)
_CLATSOP_ZONING_REST_BASE = (
    "https://delta.co.clatsop.or.us/server/rest/services/"
    "Zoning_Layers/MapServer"
)


def _clatsop_l1_row(taxlot: str):
    """Fetch the L1 row for a Clatsop taxlot. Resolves on ORTaxlot (29-char
    primary natural key) first, then the short Taxlot, the taxmap keys, and a
    numeric ACCOUNT_ID for broker convenience. Acreage is computed geodesically
    from geom (the source carries no acreage column)."""
    if not taxlot:
        return None
    canon = taxlot.strip().upper()
    acct = None
    if canon.isdigit():
        try:
            acct = int(canon)
        except (TypeError, ValueError):
            acct = None
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT id, objectid, taxlot, county_fips,
                          ortaxlot, taxlot_short, taxmapnum, taxmapkey, taxlotkey,
                          map_number, si_map_tax, account_id,
                          owner_line, owner_ll_1, owner_ll_2, in_care_of,
                          street_add, optional_a, po_box, unit_number,
                          mail_city, mail_state, mail_zip,
                          situs_address, situs_city,
                          property_class, stat_class, year_built, tax_code,
                          ma, nh, septic_status, own_sit_lot,
                          shape_area,
                          ROUND((ST_Area(geom::geography) / 4046.8564224)::numeric, 3) AS acres,
                          ingested_at, last_modified
                   FROM parcels_clatsop
                   WHERE taxlot = %s OR taxlot_short = %s OR taxmapkey = %s
                         OR taxlotkey = %s OR account_id = %s
                   LIMIT 1""",
                (canon, canon, canon, canon, acct),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[clatsop_l1_row] DB error: {e}")
        return None


def _clatsop_owner_names(row) -> list[str]:
    out = []
    for k in ("owner_line", "owner_ll_1", "owner_ll_2"):
        v = (row[k] or "").strip()
        if v:
            out.append(v)
    return out


def _clatsop_mailing(row) -> dict:
    return {
        "street": (row["street_add"] or "").strip() or None,
        "street_2": (row["optional_a"] or "").strip() or None,
        "unit": (row["unit_number"] or "").strip() or None,
        "po_box": (row["po_box"] or "").strip() or None,
        "city": (row["mail_city"] or "").strip() or None,
        "state": (row["mail_state"] or "").strip() or None,
        "zip": (row["mail_zip"] or "").strip() or None,
    }


def _clatsop_situs(row) -> str | None:
    s = (row["situs_address"] or "").strip()
    if not s:
        return None
    city = (row["situs_city"] or "").strip()
    return f"{s}, {city}" if city else s


def fetch_clatsop_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Clatsop assessment + ownership + situs snapshot for a taxlot.

    Pass-through over parcels_clatsop L1 inline (no external HTTP). Returns
    up-to-three owner lines (+ in-care-of) / mailing block / situs / account /
    property class / year built / septic status / geodesic acreage, plus
    verified deep links to the Clatsop County Public Property Search,
    Assessment & Taxation, and Webmaps surfaces.

    IN FLIGHT — honestly flagged, never invented:
      - Valuation (assessed / RMV) — the public Taxlots layer carries NO
        valuation fields; Clatsop valuation lives in the token-gated
        Assessment_Tax folder. Broker pulls it from Public Property Search.
      - Zoning — not on the taxlot record; lives on a separate queryable
        Zoning_Layers MapServer (county + Astoria / Cannon Beach / Gearhart /
        Seaside / Warrenton). Zoning ingest queued (Jackson pattern).
      - Building detail beyond year built (sqft / beds / baths) — not in source.

    Returns: {found, taxlot, county_fips, snapshot{owner, situs, account,
              classification, acreage}, deep_links, in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _clatsop_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_clatsop", "taxlot": canon}

    owners = _clatsop_owner_names(row)

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_clatsop L1 inline (Clatsop County Taxlots FeatureServer) + Clatsop County deep links",
        "snapshot": {
            "owner": {
                "primary": owners[0] if owners else None,
                "all_owners": owners,
                "owner_count": len(owners),
                "in_care_of": (row["in_care_of"] or "").strip() or None,
                "mailing": _clatsop_mailing(row),
            },
            "situs": {
                "address": _clatsop_situs(row),
                "raw_situs": row["situs_address"],
                "city": row["situs_city"],
            },
            "account": {
                "account_id": row["account_id"],
                "taxlot_short": row["taxlot_short"],
                "taxmapnum": row["taxmapnum"],
                "taxmapkey": row["taxmapkey"],
                "taxlotkey": row["taxlotkey"],
                "map_number": row["map_number"],
                "si_map_tax": row["si_map_tax"],
                "tax_code": row["tax_code"],
                "maintenance_area": row["ma"],
                "neighborhood": row["nh"],
            },
            "classification": {
                "property_class": row["property_class"],
                "stat_class": row["stat_class"],
                "year_built": int(row["year_built"]) if row["year_built"] else None,
                "septic_status": row["septic_status"],
            },
            "acreage": {
                "geodesic_acres": float(row["acres"]) if row["acres"] is not None else None,
                "shape_area_sqft": float(row["shape_area"]) if row["shape_area"] is not None else None,
            },
            "valuation": {
                "assessed_total": None,
                "rmv_total": None,
                "land_value": None,
                "improvement_value": None,
            },
        },
        "deep_links": {
            "public_property_search": _CLATSOP_PROPERTY_SEARCH,
            "assessment_taxation": _CLATSOP_ASSESSMENT,
            "webmaps_property_info": _CLATSOP_WEBMAPS,
            "property_records": _CLATSOP_PROPERTY_RECORDS,
            "gis_maps": _CLATSOP_GIS_MAPS,
            "transient_room_tax": _CLATSOP_TRANSIENT_ROOM_TAX,
            "taxlot_rest": (
                f"{_CLATSOP_TAXLOT_REST_BASE}/query?"
                f"where=ORTaxlot%3D%27{row['taxlot']}%27&outFields=*&f=json"
            ),
        },
        "in_flight": [
            "Valuation (assessed / RMV, land / improvement split) — the public Clatsop Taxlots layer exposes NO valuation fields; valuation lives in the token-gated Assessment_Tax folder. Broker pulls it from Public Property Search (clatsopcounty.gov/244) using the account_id. Per-account scrape queued behind token / Playwright.",
            "Zoning — not on the taxlot record. Clatsop publishes a separate queryable Zoning_Layers MapServer (county + Astoria / Cannon Beach / Gearhart / Seaside / Warrenton). A clatsop_zoning ingest + LATERAL spatial join is queued (Jackson pattern); until then zoning is null.",
            "Building detail beyond year built (sqft / bedrooms / baths) — not exposed by the public feed. Behind the Public Property Search surface.",
            "Recorded sale price / chain of title — not in this layer (no instrument fields). See fetch_clatsop_records.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_clatsop_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Clatsop recorded-document snapshot for a taxlot.

    Returns the current owner(s) of record from parcels_clatsop L1 inline plus
    deep links to the Clatsop County Property Records and Public Records Request
    surfaces. The recorded-instrument chain (deed history, sale price, instrument
    numbers) is NOT exposed by the public Taxlots layer — the entire recording
    chain is IN FLIGHT, surfaced via deep link until a structured recording source
    is wired. Honest framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _clatsop_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_clatsop", "taxlot": canon}

    owners = _clatsop_owner_names(row)

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_clatsop L1 inline owner-of-record + Clatsop County recording deep links",
        "latest_recorded_sale": None,
        "owner_of_record": {
            "primary": owners[0] if owners else None,
            "all_owners": owners,
            "owner_count": len(owners),
            "in_care_of": (row["in_care_of"] or "").strip() or None,
            "mailing": _clatsop_mailing(row),
        },
        "deep_links": {
            "property_records": _CLATSOP_PROPERTY_RECORDS,
            "public_records_request": _CLATSOP_PUBLIC_RECORDS,
            "public_property_search": _CLATSOP_PROPERTY_SEARCH,
            "assessment_taxation": _CLATSOP_ASSESSMENT,
        },
        "in_flight": [
            "Full recorded-instrument chain of title (deed history, instrument numbers, recording dates) — NOT exposed by the public Clatsop Taxlots layer; no instrument fields in source. The County Clerk / Property Records surface is the broker hand-off until a structured recording source is wired.",
            "Recorded sale price — not in source. Behind the recording / Public Property Search surface.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_clatsop_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Clatsop (FIPS 007).

    Clatsop is Tier 3 Standard. Per the established pattern (Tier 1 Flagships run
    county-direct Accela tenants; Tier 2/3/4 ride state Accela), all Clatsop
    jurisdictions — Astoria, Seaside, Cannon Beach, Gearhart, Warrenton, and
    unincorporated Clatsop — default-route through Oregon's state ePermitting
    (aca-oregon.accela.com /oregon). Clatsop FIPS '007' is in
    _OREGON_EPERMITTING_FIPS so this delegates cleanly.

    No dedicated Clatsop permits GIS FeatureServer exists (the county
    Code_Compliance MapServer is code enforcement, not building permits), so
    the state-Accela deep link is the canonical surface. Honest IN FLIGHT
    framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    result = fetch_county_permits(taxlot, "007", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Clatsop County (Astoria / Seaside / Cannon Beach / Gearhart / "
            "Warrenton / unincorporated — Oregon state Accela; Tier 3 reuse pattern)"
        )
    return result
