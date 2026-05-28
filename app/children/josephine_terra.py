"""
JOSEPHINE County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, Tier 2 Major #6).

Lives in its own module — same precedent linn_terra / polk_terra established —
so concurrent Yindo-session edits on orb_db.py during multi-county expansions
don't collide. Re-exported from orb_db.py via
`from .josephine_terra import fetch_josephine_*` so the public contract matches
every other county.

Reality of the Josephine integration:

  1. parcels_josephine L1 is the RICHEST L1 inline surface TERRA has shipped to
     date. The single on-prem Assessor_Taxlots FeatureServer
     (gis.co.josephine.or.us/.../Assessor/Assessor_Taxlots/FeatureServer/0)
     carries ALL of: ownership (NAME + full mailing block), situs (parsed),
     account (R-number), valuation (RMV + assessed + improvement + land,
     market AND appraised), acreage (3 sources), property class + building
     class, building details (year built, sqft, living-area, bedrooms), tax
     code + neighborhood, ZONING INLINE (Zone field — no separate service
     needed, unlike Jackson), AND the most-recent recorded sale fully
     decomposed (SALE_DATE, SALE_PRICE, DEED_TYPE, INST_NO, SALE_TYPE).

     So fetch_josephine_assessment AND fetch_josephine_records are BOTH SQL
     pass-throughs over L1 inline — no scraper, no viewstate gating, no
     external HTTP per request. Cleanest L2 ship in TERRA.

  2. What L1 does NOT ship: full multi-instrument chain of title (only the
     latest is inline) and tax-payment history. The Clerk's recording office
     publishes a recorded-document research surface
     (`https://josephinecounty.gov/government/county_clerk_recording/index.php`)
     and the Assessor publishes a Property Data lookup tool
     (`https://jcpa.josephinecounty.gov/Home`) — both are session-gated
     (JCPA = ASP.NET viewstate; recording uses the Tessera Public Records
     index that won't yield to a simple GET). Deep links surfaced; structured
     scrape flagged IN FLIGHT.

  3. Permits — Josephine jurisdictions split, and this is the FIRST TERRA
     county where a city-direct portal is EnerGov (Tyler Technologies) rather
     than Accela:

       Grants Pass   → EnerGov SelfService at
                       https://selfservice.grantspassoregon.gov/energov_prod/selfservice
                       (city-direct self-service portal, NOT state Accela).
                       Deep-link IN FLIGHT — EnerGov SelfService accepts URL
                       query params for parcel/address search but the schema
                       is not yet captured; surfacing the portal landing as
                       the broker hand-off.

       Cave Junction + unincorporated → Oregon state ePermitting Accela
                       (Josephine FIPS '033' added to _OREGON_EPERMITTING_FIPS).

     fetch_josephine_permits routes by situs_city: Grants Pass → EnerGov
     surface; everything else (Cave Junction, Merlin, Selma, Williams, Wolf
     Creek, O'Brien, Wilderville, Kerby, Sunny Valley, Applegate, plus all
     unspecified situs) → state Accela via fetch_county_permits.

     This mirrors the Linn pattern (Albany on its own EnerGov-adjacent
     city portal, rest of county on state Accela) and validates the
     emerging Tier 2 Major routing rule: county-seat-may-run-own-portal,
     everything-else-rides-state.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


# Josephine assessor + clerk + permitting deep-link targets
_JOSEPHINE_ASSESSOR_PROPERTY_DATA = "https://jcpa.josephinecounty.gov/Home"
_JOSEPHINE_ASSESSOR_HOMEPAGE = (
    "https://josephinecounty.gov/government/assessor/index.php"
)
_JOSEPHINE_CLERK_RECORDING = (
    "https://josephinecounty.gov/government/county_clerk_recording/index.php"
)
_JOSEPHINE_CLERK_HOMEPAGE = (
    "https://josephinecounty.gov/government/county_clerk/index.php"
)
_JOSEPHINE_GIS_HUB = "https://josephinecounty.gov/gis"
_JOSEPHINE_TAXLOT_REST_BASE = (
    "https://gis.co.josephine.or.us/arcgis/rest/services/"
    "Assessor/Assessor_Taxlots/FeatureServer/0"
)
_JOSEPHINE_PROPERTY_MAP = (
    "https://joco.maps.arcgis.com/apps/webappviewer/index.html"
)

# Permitting surfaces — Grants Pass runs its own EnerGov tenant; everything else
# rides state Accela.
_GRANTS_PASS_ENERGOV_SELFSERVICE = (
    "https://selfservice.grantspassoregon.gov/energov_prod/selfservice"
)
_GRANTS_PASS_CITY_PERMITS_PAGE = (
    "https://www.grantspassoregon.gov/237/Permits"
)


def _josephine_l1_row(taxlot: str):
    """Fetch the L1 row for a Josephine taxlot. Resolves on either MapNum
    (canonical 14-char) or the MNX 16-char extended form, falling back to a
    case-insensitive ACCOUNT (R-number) match for broker convenience."""
    if not taxlot:
        return None
    canon = taxlot.strip().upper()
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT id, objectid, taxlot, county_fips,
                          map_num, mnx, sd, twn, rng, sec, qq, taxlot_num,
                          type, loc_desc, lot, lot_1, block,
                          account, acct_status, owner_name,
                          mailing_address, mailing_addr1, mailing_addr2, mailing_addr3,
                          mailing_city, mailing_state, mailing_zip, mailing_csz,
                          situs_address, situs_city, situs_state, situs_zip,
                          st_no, situs_pref, st_name, situs_suff, situs_suf0,
                          appr_value, assd_value, imp_value, land_appr, land_mkt, rmv, mh_value,
                          acreage, legal_acre, gis_acres,
                          prop_class, bldg_class, code, maint, nbhd, sptb_codes, exempt, taxes,
                          yr_blt, sq_ft, living_area, bedrms, mh_make, comp_mtl,
                          zone,
                          sale_date, sale_price, deed_type, inst_no, sale_type,
                          asr_latitude, asr_longitude,
                          ingested_at, last_modified
                   FROM parcels_josephine
                   WHERE taxlot = %s OR map_num = %s OR mnx = %s OR account = %s
                   LIMIT 1""",
                (canon, canon, canon, canon),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[josephine_l1_row] DB error: {e}")
        return None


def _josephine_situs_full(row) -> str | None:
    """Compose a clean situs string. Source SITUS field sometimes starts with
    `*` (sentinel for unassigned/road) — strip when present."""
    s = row["situs_address"]
    if not s:
        return None
    s = s.strip()
    if s.startswith("*"):
        s = s.lstrip("*").strip()
    if not s:
        return None
    city = (row["situs_city"] or "").strip()
    return f"{s}, {city}" if city else s


def fetch_josephine_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Josephine assessment + ownership + zoning + recent-sale snapshot.

    Pass-through over parcels_josephine L1 inline (no external HTTP). Returns
    owner / mailing / situs / account / acreage / valuation (both RMV
    real-market AND assessed) / building details / inline zoning / most-recent
    sale, plus verified deep links to the Josephine County Property Assessor
    (JCPA) lookup and the Clerk's Recording office.

    Cleanest L2 surface TERRA has shipped — Josephine's single Assessor_Taxlots
    service is the richest inline data of any Oregon county we've touched.

    IN FLIGHT — honestly flagged, never invented:
      - JCPA per-account deep-link parameterization (the property lookup is
        ASP.NET viewstate-gated — the broker clicks through and pastes the
        ACCOUNT to land on the parcel detail).
      - Multi-year tax payment history — same viewstate gating as Marion's
        MCASR and Jackson's PDO; queued behind viewstate capture or paid tier.

    Returns: {found, taxlot, county_fips, snapshot{owner, situs, account,
              acreage, valuation, building, zoning, recent_sale}, deep_links,
              in_flight[], fetched_at, from_cache, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _josephine_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_josephine", "taxlot": canon}

    appr = row["appr_value"]
    assd = row["assd_value"]
    imp = row["imp_value"]
    land_appr = row["land_appr"]
    land_mkt = row["land_mkt"]
    rmv = row["rmv"]

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_josephine L1 inline (Josephine County Assessor_Taxlots FeatureServer) + JCPA deep link",
        "snapshot": {
            "owner": {
                "primary": row["owner_name"],
                "mailing_address": row["mailing_address"],
                "mailing_addr1": row["mailing_addr1"],
                "mailing_addr2": row["mailing_addr2"],
                "mailing_addr3": row["mailing_addr3"],
                "mailing_city": row["mailing_city"],
                "mailing_state": row["mailing_state"],
                "mailing_zip": row["mailing_zip"],
                "mailing_csz": row["mailing_csz"],
            },
            "situs": {
                "address": _josephine_situs_full(row),
                "raw_situs": row["situs_address"],
                "city": row["situs_city"],
                "state": row["situs_state"],
                "zip": row["situs_zip"],
                "street_number": row["st_no"],
                "street_prefix": row["situs_pref"],
                "street_name": row["st_name"],
                "street_suffix": row["situs_suff"],
            },
            "account": {
                "number": row["account"],
                "status": row["acct_status"],
                "map_num": row["map_num"],
                "mnx": row["mnx"],
                "school_district": row["sd"],
                "township_range_section": (
                    f"{row['twn'] or ''}-{row['rng'] or ''}-{row['sec'] or ''}-{row['qq'] or ''}"
                    if (row["twn"] or row["rng"] or row["sec"]) else None
                ),
                "taxlot_num": row["taxlot_num"],
                "lot": row["lot"] or row["lot_1"],
                "block": row["block"],
                "prop_class": row["prop_class"],
                "bldg_class": row["bldg_class"],
                "tax_code": row["code"],
                "neighborhood": row["nbhd"],
                "maintenance_area": row["maint"],
            },
            "acreage": {
                "assessor_of_record": float(row["acreage"]) if row["acreage"] is not None else None,
                "legal_acres": float(row["legal_acre"]) if row["legal_acre"] is not None else None,
                "gis_acres": float(row["gis_acres"]) if row["gis_acres"] is not None else None,
            },
            "valuation": {
                "rmv_total": rmv,
                "rmv_land_market": land_mkt,
                "rmv_land_appraised": land_appr,
                "rmv_improvement": imp,
                "assessed_total": assd,
                "appraised_total": appr,
                "manufactured_home_value": row["mh_value"],
                "annual_tax": row["taxes"],
                "exempt_code": row["exempt"],
            },
            "building": {
                "year_built": int(row["yr_blt"]) if row["yr_blt"] else None,
                "total_sqft": float(row["sq_ft"]) if row["sq_ft"] else None,
                "living_area_sqft": float(row["living_area"]) if row["living_area"] else None,
                "bedrooms": int(row["bedrms"]) if row["bedrms"] else None,
                "manufactured_home_make": row["mh_make"],
                "comp_mtl": row["comp_mtl"],
            },
            "zoning": {
                "code": row["zone"],
                "source": "inline parcels_josephine.zone (no separate zoning service needed)",
            },
            "recent_sale": {
                "date": row["sale_date"].isoformat() if row["sale_date"] else None,
                "price": float(row["sale_price"]) if row["sale_price"] else None,
                "deed_type": row["deed_type"],
                "instrument_no": row["inst_no"],
                "sale_type": row["sale_type"],
            } if (row["sale_date"] or row["inst_no"]) else None,
        },
        "deep_links": {
            "property_data_lookup": _JOSEPHINE_ASSESSOR_PROPERTY_DATA,
            "assessor_home": _JOSEPHINE_ASSESSOR_HOMEPAGE,
            "clerk_recording": _JOSEPHINE_CLERK_RECORDING,
            "gis_hub": _JOSEPHINE_GIS_HUB,
            "taxlot_rest": (
                f"{_JOSEPHINE_TAXLOT_REST_BASE}/query?where=ACCOUNT%3D%27{row['account']}%27"
                "&outFields=*&f=json"
            ) if row["account"] else None,
        },
        "in_flight": [
            "Property Data Lookup (JCPA) per-account deep-link parameterization — the lookup is ASP.NET viewstate-gated; the broker clicks through and pastes the ACCOUNT (R-number) to land on the parcel detail page.",
            "Multi-year tax payment history — same viewstate gating as Marion's MCASR and Jackson's PDO; coming once viewstate capture or paid tier lands.",
            "Full multi-instrument chain of title — only the most-recent sale (DEED_TYPE / INST_NO / SALE_DATE / SALE_PRICE) is inline; full chain lives in the Clerk's Tessera Public Records index, surfaced via deep link until structured scrape lands.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_josephine_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Josephine Clerk recorded-instrument snapshot for a taxlot.

    Returns the most-recent recorded sale from parcels_josephine L1 inline
    (Josephine's Assessor_Taxlots service ships the latest DEED_TYPE, INST_NO,
    SALE_DATE, SALE_PRICE inline — richer than Jackson which ships owner only)
    plus the current owner of record and deep links to the Clerk's recording
    office.

    The multi-instrument deed chain is IN FLIGHT — the Clerk's recorded-document
    index runs on the Tessera Public Records platform with session-gated
    retrieval (same viewstate-style blocker we hit on Marion / Washington /
    Jackson Clerk surfaces). Honest framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _josephine_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_josephine", "taxlot": canon}

    latest_sale = None
    if row["sale_date"] or row["inst_no"]:
        latest_sale = {
            "date": row["sale_date"].isoformat() if row["sale_date"] else None,
            "price": float(row["sale_price"]) if row["sale_price"] else None,
            "deed_type": row["deed_type"],
            "instrument_no": row["inst_no"],
            "sale_type": row["sale_type"],
        }

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_josephine L1 inline latest-sale + owner-of-record + Josephine Clerk deep links",
        "latest_recorded_sale": latest_sale,
        "owner_of_record": {
            "primary": row["owner_name"],
            "mailing_address": row["mailing_address"],
            "mailing_city": row["mailing_city"],
            "mailing_state": row["mailing_state"],
            "mailing_zip": row["mailing_zip"],
            "mailing_csz": row["mailing_csz"],
        },
        "deep_links": {
            "clerk_recording": _JOSEPHINE_CLERK_RECORDING,
            "clerk_home": _JOSEPHINE_CLERK_HOMEPAGE,
            "property_data_lookup": _JOSEPHINE_ASSESSOR_PROPERTY_DATA,
            "assessor_home": _JOSEPHINE_ASSESSOR_HOMEPAGE,
        },
        "in_flight": [
            "Full multi-instrument chain of title — only the most-recent sale (DEED_TYPE / INST_NO / SALE_DATE / SALE_PRICE) is inline. Josephine Clerk runs the Tessera Public Records index — session-gated, not URL-deep-linkable to a specific parcel; the broker uses the deep link to search by instrument number or grantor/grantee.",
            "Recorded plat / partition / easement instruments separate from the conveyance chain — not in the parcels service. Tessera or in-person research until structured scrape lands.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_josephine_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Josephine permits — routes by situs_city.

    Grants Pass     → EnerGov SelfService (city-direct, NOT state Accela).
                      First EnerGov surface in TERRA. Deep link only; the
                      SelfService URL accepts search query strings but the
                      per-parcel deep-link parameter map isn't yet captured.
    Cave Junction +
      everything else → Oregon state ePermitting Accela (Josephine FIPS '033'
                      is in _OREGON_EPERMITTING_FIPS so this delegates to
                      fetch_county_permits).

    Returns the same shape as every other county's permits fetcher:
    {found, taxlot, county_fips, jurisdiction, situs_city, status,
     permit_count, permits, deep_links, in_flight[], fetched_at, ...}.

    Honest IN FLIGHT framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _josephine_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_josephine", "taxlot": canon}

    situs_city = (row["situs_city"] or "").strip().upper()
    raw_situs = row["situs_address"]

    # Grants Pass branch — EnerGov SelfService.
    if situs_city == "GRANTS PASS":
        return {
            "found": True,
            "from_cache": False,
            "taxlot": row["taxlot"],
            "county_fips": row["county_fips"],
            "jurisdiction": "City of Grants Pass (EnerGov SelfService — Tyler Technologies)",
            "situs_city": row["situs_city"],
            "situs_address": _josephine_situs_full(row),
            "routed_via": "grants_pass_energov_selfservice",
            "status": "deep_link_only",
            "permit_count": 0,
            "permits": [],
            "deep_links": {
                "energov_selfservice": _GRANTS_PASS_ENERGOV_SELFSERVICE,
                "city_permits_page": _GRANTS_PASS_CITY_PERMITS_PAGE,
                "assessor_data_lookup": _JOSEPHINE_ASSESSOR_PROPERTY_DATA,
            },
            "in_flight": [
                "EnerGov SelfService per-parcel deep-link parameterization — the SelfService surface accepts free-text search by address / permit number / parcel, but the URL-deep-link query string for a specific Josephine parcel isn't yet captured. Broker pastes the situs address or ACCOUNT into the SelfService search.",
                "First EnerGov (Tyler Technologies) surface in TERRA — Grants Pass joins a small set of Oregon cities running EnerGov rather than Accela. Worth wiring a reusable fetch_energov_permits helper once parameterization is captured (Tier 3 / Tier 4 follow-up).",
                "Permit history for the broker hand-off is at the SelfService landing — clicking the link lands them at the search page where pasting the situs returns the live permit list.",
            ],
            "fetched_at": _dt.now(_tz.utc).isoformat(),
        }

    # Everything else — Cave Junction + unincorporated + smaller jurisdictions
    # → state Accela.
    result = fetch_county_permits(canon, "033", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["situs_city"] = row["situs_city"]
        result["situs_address"] = _josephine_situs_full(row)
        jurisdiction_note = situs_city.title() if situs_city else "unincorporated"
        result["jurisdiction"] = (
            f"Josephine County ({jurisdiction_note} — Oregon state Accela; "
            "Tier 2 reuse pattern, non-Grants-Pass jurisdictions)"
        )
    return result
