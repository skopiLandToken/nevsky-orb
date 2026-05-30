"""
COLUMBIA County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-30, county #19).

Lives in its own module (same precedent polk_terra.py / linn_terra.py established)
so parallel-Yindo-session edits on orb_db.py don't collide. Re-exported from
orb_db.py via `from .columbia_terra import fetch_columbia_*` so the public contract
matches every other county (`from app.children.orb_db import fetch_columbia_*`).

Reality of the Columbia integration:

  1. parcels_columbia L1 is POLK/MARION-RICH: the Columbia County TaxlotWb
     FeatureServer (gis.columbiacountymaps.com/server/rest/services/TaxlotWb/
     FeatureServer/0) ships polygon attrs JOINED to the assessor account in one
     row. Owner, agent, full mailing block, PRIMARY_SI (combined situs string),
     ACCOUNT_ID, property class, residence/building counts, acreage + sqft, and
     ACCELA_MT (the pre-formatted Accela map-taxlot key) are all inline. So
     fetch_columbia_assessment and fetch_columbia_records are SQL pass-throughs
     over L1 — NO scraper, NO viewstate gating, NO HTTP per request.

  2. What L1 does NOT ship inline: valuation (the public REST layer carries
     NEITHER assessed NOR RMV — values live in the bi-weekly Tax26.mdb export +
     the assessor portal), building details (year built / bed / bath — only
     dwelling + building COUNTS are present), the recorded deed/sale chain (the
     county publishes a SEPARATE `Sales` FeatureServer — Good Sales 1/2/3-yrs-ago,
     queryable — queued as a records L3 follow-up), and zoning (Land_Development
     folder not yet probed for a zoning layer). All flagged IN FLIGHT.

  3. Public surfaces: the county Assessor landing is at columbiacountyor.gov/
     departments/Assessor; the Cartography/GIS page carries the bi-weekly
     shapefile + Tax26 data downloads. NOTE — the interactive ColumbiaCountyWebMaps
     app went OFFLINE 2026-04-22 for an ADA/WCAG Title-II upgrade, so the
     human-facing deep link surfaces the Assessor/GIS landing pages (live) plus
     the REST taxlot query (live) rather than the web-map app (down). The Clerk
     recording office landing is the chain-of-title hand-off.

  4. Permits — Columbia rides Oregon's state ePermitting (aca-oregon.accela.com/
     oregon); confirmed via columbiacountyor.gov/departments/Building (structural/
     plumbing/mechanical/electrical online via the State portal). Columbia FIPS
     '009' added to _OREGON_EPERMITTING_FIPS in orb_db. fetch_columbia_permits is
     a one-line wrapper around fetch_county_permits.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


_COLUMBIA_ASSESSOR_HOMEPAGE = "https://www.columbiacountyor.gov/departments/Assessor"
_COLUMBIA_GIS_PAGE = (
    "https://www.columbiacountyor.gov/departments/Assessor/"
    "CartographyandGeographicInformationSystem"
)
_COLUMBIA_CLERK_HOMEPAGE = "https://www.columbiacountyor.gov/departments/Clerk"
_COLUMBIA_BUILDING_HOMEPAGE = "https://www.columbiacountyor.gov/departments/Building"
_COLUMBIA_TAXLOT_REST_BASE = (
    "https://gis.columbiacountymaps.com/server/rest/services/TaxlotWb/FeatureServer/0"
)


def _columbia_l1_row(taxlot: str):
    """Fetch the L1 row for a Columbia taxlot.

    Resolves against MAP_TAX (the natural taxlot key, mirrored into both taxlot and
    maptaxlot) first, then against ACCELA_MT (the Accela-formatted variant) so a
    caller holding either form resolves. Returns dict or None.
    """
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, county_fips,
                          maptaxlot, map_number, accela_mt, account_id,
                          owner_line1, agent_name,
                          mail_add1, mail_city, mail_state, mail_zip,
                          primary_situs, site_add_nam, site_add_cty, site_zip,
                          prp_class, num_houses, num_buildings, dwelling,
                          taxlot_acres, taxlot_sqft, size_label,
                          ingested_at, last_modified
                   FROM parcels_columbia
                   WHERE taxlot = %s OR maptaxlot = %s OR accela_mt = %s
                   LIMIT 1""",
                (taxlot, taxlot, taxlot),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[columbia_l1_row] DB error: {e}")
        return None


def _columbia_mailing_csz(row) -> str | None:
    """Compose `CITY, ST  ZIP` from the mailing-address columns."""
    city = (row["mail_city"] or "").strip() or None
    state = (row["mail_state"] or "").strip() or None
    zipc = (row["mail_zip"] or "").strip() or None
    if not (city or state or zipc):
        return None
    left = ", ".join(p for p in (city, state) if p) or None
    return f"{left}  {zipc}".strip() if left and zipc else (left or zipc)


def _columbia_acres(row):
    """taxlot_acres is the assessor-of-record value; fall back to a sqft->acre
    conversion for the city lots that carry SQFT instead of an ACRES value."""
    a = row["taxlot_acres"]
    if a is not None and float(a) > 0:
        return round(float(a), 3)
    sqft = row["taxlot_sqft"]
    if sqft and int(sqft) > 0:
        return round(int(sqft) / 43560.0, 3)
    return None


def fetch_columbia_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Columbia assessment + ownership snapshot for a taxlot.

    Pass-through over parcels_columbia L1 inline data (no external HTTP). Returns
    owner / agent / mailing block / situs / account / property class / dwelling +
    building counts / acreage, plus deep links to the Columbia County Assessor +
    GIS landing pages and the live REST taxlot query.

    What's IN FLIGHT:
      - Valuation — the public TaxlotWb REST layer ships NO assessed and NO RMV
        values (unlike Polk's assessed-only or Douglas's full RMV). Values live
        in the bi-weekly Tax26.mdb export + the assessor portal. Caller surfaces
        null valuation; do not invent.
      - Building details (year built / sqft-per-structure / bed / bath) — only
        dwelling + building COUNTS are exposed by source.
      - Zoning — Columbia's Land_Development folder has not yet been mapped to a
        zoning layer. Queued behind the L1 ship.

    Returns: {found, taxlot, county_fips, snapshot{owner,situs,account,
              valuation,acreage,structures}, deep_links, in_flight[],
              fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _columbia_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_columbia", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_columbia L1 inline (Columbia County TaxlotWb FeatureServer, account joined to polygon) + Assessor/GIS deep links",
        "snapshot": {
            "owner": {
                "primary": row["owner_line1"],
                "agent": row["agent_name"],
                "mailing_address_1": row["mail_add1"],
                "mailing_csz": _columbia_mailing_csz(row),
            },
            "situs": {
                "address": row["primary_situs"],
                "street": row["site_add_nam"],
                "city": row["site_add_cty"],
                "zip": row["site_zip"],
            },
            "account": {
                "account_id": row["account_id"],
                "accela_map_taxlot": row["accela_mt"],
                "map_number": row["map_number"],
                "prop_class": row["prp_class"],
                "dwelling": row["dwelling"],
            },
            "valuation": {
                "assessed_land": None,
                "assessed_improvement": None,
                "assessed_total": None,
                "rmv_land": None,
                "rmv_improvement": None,
                "rmv_total": None,
            },
            "acreage": {
                "taxlot_acres": _columbia_acres(row),
                "taxlot_sqft": row["taxlot_sqft"],
                "size_label": row["size_label"],
            },
            "structures": {
                "num_residences": row["num_houses"],
                "num_buildings": row["num_buildings"],
            },
        },
        "deep_links": {
            "assessor_home": _COLUMBIA_ASSESSOR_HOMEPAGE,
            "gis_data": _COLUMBIA_GIS_PAGE,
            "taxlot_rest": f"{_COLUMBIA_TAXLOT_REST_BASE}/query?where=MAP_TAX%3D%27{row['taxlot']}%27&outFields=*&f=json",
        },
        "in_flight": [
            "Valuation (assessed AND real-market) — Columbia's public TaxlotWb REST layer exposes neither; values live in the bi-weekly Tax26.mdb export + the assessor portal. Surfaced as null; broker pulls valuation via the account number at the Assessor's office.",
            "Building details (year built, per-structure sqft, bedrooms, baths) — source ships only dwelling + building COUNTS, not per-structure detail.",
            "Zoning — Columbia's Land_Development ArcGIS folder not yet mapped to a columbia_zoning table. Queued behind the L1 ship.",
            "Interactive county web map (ColumbiaCountyWebMaps) offline since 2026-04-22 for an ADA/WCAG upgrade — deep link surfaces the live Assessor/GIS landing pages + REST query instead of the web-map app.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_columbia_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Columbia owner-of-record + recording hand-off for a taxlot.

    Pass-through over parcels_columbia L1 inline data (no external HTTP). The
    Columbia TaxlotWb layer does NOT carry an inline instrument snapshot (unlike
    Polk/Marion which ship the last instrument id/type/year/month), so v1 returns
    the current owner of record + account + a deep link to the Columbia County
    Clerk recording office. The county's separate `Sales` FeatureServer (Good
    Sales 1/2/3-yrs-ago, queryable) is queued as the L3 sale-chain follow-up.

    What's IN FLIGHT:
      - Recorded deed chain + instrument detail — not inline in TaxlotWb. The
        Clerk recording office is the human hand-off; a per-parcel sale fetch
        off the `Sales` FeatureServer is queued.
      - Recorded sale price — same; lives on the `Sales` FeatureServer.

    Returns: {found, taxlot, county_fips, latest_instrument(None — IN FLIGHT),
              owner_of_record, deep_links, in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _columbia_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_columbia", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_columbia L1 inline (owner of record) + Columbia County Clerk recording-office deep link",
        "latest_instrument": None,  # not inline in TaxlotWb — IN FLIGHT (Sales FeatureServer follow-up)
        "owner_of_record": {
            "primary": row["owner_line1"],
            "agent": row["agent_name"],
            "mailing_address_1": row["mail_add1"],
            "mailing_csz": _columbia_mailing_csz(row),
            "account_id": row["account_id"],
        },
        "deep_links": {
            "clerk_recording_office": _COLUMBIA_CLERK_HOMEPAGE,
            "assessor_home": _COLUMBIA_ASSESSOR_HOMEPAGE,
            "gis_data": _COLUMBIA_GIS_PAGE,
        },
        "in_flight": [
            "Recorded deed chain + instrument detail (id / type / date) — NOT inline in the Columbia TaxlotWb layer. The Columbia County Clerk recording office (deep link) is the chain-of-title hand-off; a per-parcel fetch off the county `Sales` FeatureServer (Good Sales 1/2/3-yrs-ago) is queued as the L3 follow-up.",
            "Recorded sale price — same; lives on the `Sales` FeatureServer, queued.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_columbia_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Columbia (FIPS 009).

    Columbia rides Oregon's state ePermitting (aca-oregon.accela.com/oregon) —
    confirmed via columbiacountyor.gov/departments/Building (structural / plumbing
    / mechanical / electrical applied online through the State portal). Columbia
    FIPS '009' is in _OREGON_EPERMITTING_FIPS so this delegates cleanly. When the
    caller holds the ACCELA_MT key (stored on the parcel row), it maps 1:1 to the
    Accela map-taxlot search the deep link drives.
    """
    result = fetch_county_permits(taxlot, "009", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Columbia County (St. Helens / Scappoose / Rainier / Clatskanie / "
            "Vernonia / Columbia City / Prescott / unincorporated — Oregon state "
            "Accela; Tier reuse pattern)"
        )
    return result
