"""
POLK County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, county #10).

Lives in its own module (same precedent linn_terra.py established) so the
parallel-Yindo-session edits on orb_db.py don't collide. Re-exported from
orb_db.py via `from .polk_terra import fetch_polk_*` so the public contract
matches every other county (`from app.children.orb_db import fetch_polk_*`).

Reality of the Polk integration:

  1. parcels_polk L1 is Marion-rich: the Polk County Taxlots MapServer
     (maps.co.polk.or.us/gis/rest/services/Assessor/Taxlots/MapServer/11)
     ships polygon attrs JOINED to the assessor AcctTable in a single row.
     Owner, agent, full mailing block, situs, last instrument (year/month/
     instrument-id/type), property class + description, assessed values
     (land/improvement/total), acreage, and tax routing (SA / MA / NH /
     TaxCode / TaxCodeArea / Unit_ID) are all inline. So
     fetch_polk_assessment and fetch_polk_records are SQL pass-throughs
     over L1 inline data (Marion pattern) — NO scraper, NO viewstate
     gating, NO HTTP per request.

  2. What L1 does NOT ship inline: zoning, building details (year built /
     sqft / bedrooms / bathrooms), RMV-family valuation (real-market values
     are not exposed by source — only assessed values), and the recorded
     sale price for the last instrument. The MapServer relates to
     `PCMAPS.DBO.V_REAL_SALES` (relationshipId 0 on layer 11, joining on
     AcctTable_Account_ID) which carries the sale-price chain — that's
     queryable on demand from L2 but is queued as a follow-up rather than
     blocking the L1 ship. Flagged IN FLIGHT.

  3. Public assessor portal: `https://apps2.co.polk.or.us/PSO` is Polk's
     Property Search Online (a Blazor Server app — no URL-deep-linkable
     by account_id, but the user can search by account / situs / owner /
     map after landing). Clerk recordings live in the Digital Research
     Room at `https://apps2.co.polk.or.us/DigitalResearchRoom/` (Polk
     County Clerk's 1984-present index). The www.polkcountyor.gov
     assessor + clerk landing pages live at `/228/Assessor` and via the
     /195/Maps-Records hub; these are the human-facing entry points we
     surface in deep_links.

  4. Permits — Polk is Tier 2 Major. Per the emerging pattern (Tier 1
     Flagships run county-direct Accela tenants; Tier 2/3/4 ride state
     Accela), Polk jurisdictions (Dallas, Independence, Monmouth,
     unincorporated Polk) route through Oregon's state ePermitting
     (aca-oregon.accela.com /oregon). Polk FIPS '053' added to
     _OREGON_EPERMITTING_FIPS in orb_db. fetch_polk_permits is a one-line
     wrapper around fetch_county_permits.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


_POLK_ASSESSOR_HOMEPAGE = "https://www.polkcountyor.gov/228/Assessor"
_POLK_CLERK_HOMEPAGE = "https://www.polkcountyor.gov/195/Maps-Records"
_POLK_PSO_PORTAL = "https://apps2.co.polk.or.us/PSO"
_POLK_DIGITAL_RESEARCH_ROOM = "https://apps2.co.polk.or.us/DigitalResearchRoom/"
_POLK_MAPS_PORTAL = "https://maps.co.polk.or.us/pcmaps/"
_POLK_TAXLOT_REST_BASE = (
    "https://maps.co.polk.or.us/gis/rest/services/Assessor/Taxlots/MapServer/11"
)


def _polk_l1_row(taxlot: str):
    """Fetch the L1 row for a Polk taxlot.

    Resolves against the ORTaxlot (29-char primary natural key) first, then
    against the human-friendly MapTaxlot variant. Returns dict or None.
    """
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, county_fips,
                          maptaxlot, ortaxlot, map_taxlot_short,
                          map_number, ormapnum, special_int,
                          account_id, prim_acc_num, si_map_tax,
                          owner_line1, agent_name, in_care_of,
                          mail_add1, mail_add2, mail_city, mail_state, mail_zip, mail_country,
                          site_add_nam, site_add_cty, site_zip,
                          inst_year, inst_month, inst_id, inst_type,
                          dwelling, prp_class, prp_cls_desc,
                          ast_imp_val, ast_lnd_val, ast_value,
                          sa, ma, nh, tax_code, tax_code_area, unit_id,
                          taxlot_acres, acct_acres, acct_sqft,
                          ingested_at, last_modified
                   FROM parcels_polk
                   WHERE taxlot = %s OR maptaxlot = %s
                   LIMIT 1""",
                (taxlot, taxlot),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[polk_l1_row] DB error: {e}")
        return None


def _polk_mailing_csz(row) -> str | None:
    """Compose `CITY, ST  ZIP` from the mailing-address columns."""
    city = (row["mail_city"] or "").strip() or None
    state = (row["mail_state"] or "").strip() or None
    zipc = (row["mail_zip"] or "").strip() or None
    if not (city or state or zipc):
        return None
    left = ", ".join(p for p in (city, state) if p) or None
    return f"{left}  {zipc}".strip() if left and zipc else (left or zipc)


def _polk_situs_address(row) -> str | None:
    nam = (row["site_add_nam"] or "").strip() or None
    cty = (row["site_add_cty"] or "").strip() or None
    if not nam:
        return None
    return f"{nam}, {cty}" if cty else nam


def _polk_deed_date(row) -> str | None:
    """Synthesize an ISO date from inst_year + inst_month (source has no day)."""
    y, m = row["inst_year"], row["inst_month"]
    if not y:
        return None
    if m and 1 <= int(m) <= 12:
        return f"{int(y):04d}-{int(m):02d}-01"
    return f"{int(y):04d}-01-01"


def fetch_polk_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Polk assessment + ownership snapshot for a taxlot.

    Pass-through over parcels_polk L1 inline data (no external HTTP). Returns
    owner / mailing / agent / situs / account / property class + description /
    assessed valuation (land/improvement/total) / acreage / tax routing, plus
    a deep link to the Polk County ArcGIS web map portal where the user can
    open the parcel record visually.

    What's IN FLIGHT:
      - RMV-family valuation (real-market values) — Polk's MapServer ships
        only assessed values. Real-market values are not in the public
        source. Caller surfaces null for rmv_* fields.
      - Building details (year built / sqft / bed / bath) — not exposed by
        source. Polk's per-parcel detail page (when re-published — see
        portal IN FLIGHT below) carries this; until then it's null.
      - Zoning — Polk publishes zoning on a separate MapServer not yet
        ingested. Queued behind the L1+L2 ship. Caller surfaces null.
      - Polk County's official assessor/clerk subdomain URLs are 404 as of
        the ship date; only the maps.co.polk.or.us/pcmaps ArcGIS web app
        is reachable. Deep link surfaces the maps portal until Polk
        republishes their direct portal pages.

    Returns: {found, taxlot, county_fips, snapshot{owner,situs,account,
              valuation,acreage,subdivision}, deep_links{polk_maps,
              assessor_home}, in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _polk_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_polk", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_polk L1 inline (Polk County Assessor/Taxlots MapServer joined to AcctTable) + Polk maps portal deep link",
        "snapshot": {
            "owner": {
                "primary": row["owner_line1"],
                "agent": row["agent_name"],
                "in_care_of": row["in_care_of"],
                "mailing_address_1": row["mail_add1"],
                "mailing_address_2": row["mail_add2"],
                "mailing_csz": _polk_mailing_csz(row),
                "mailing_country": row["mail_country"],
            },
            "situs": {
                "address": _polk_situs_address(row),
                "street": row["site_add_nam"],
                "city": row["site_add_cty"],
                "zip": row["site_zip"],
            },
            "account": {
                "account_id": row["account_id"],
                "prim_acc_num": row["prim_acc_num"],
                "si_map_tax": row["si_map_tax"],
                "prop_class": row["prp_class"],
                "pc_desc": row["prp_cls_desc"],
                "dwelling": row["dwelling"],
                "tax_code": row["tax_code"],
                "tax_code_area": row["tax_code_area"],
                "service_area": row["sa"],
                "mapping_area": row["ma"],
                "neighborhood": row["nh"],
                "unit_id": row["unit_id"],
            },
            "valuation": {
                "assessed_land": row["ast_lnd_val"],
                "assessed_improvement": row["ast_imp_val"],
                "assessed_total": row["ast_value"],
                "rmv_land": None,
                "rmv_improvement": None,
                "rmv_total": None,
            },
            "acreage": {
                "taxlot_acres": float(row["taxlot_acres"]) if row["taxlot_acres"] is not None else None,
                "account_acres": float(row["acct_acres"]) if row["acct_acres"] is not None else None,
                "account_sqft": row["acct_sqft"],
            },
            "subdivision": {
                "map_number": row["map_number"],
                "ormapnum": row["ormapnum"],
                "map_taxlot_short": row["map_taxlot_short"],
                "special_int": row["special_int"],
            },
        },
        "deep_links": {
            "assessor_home": _POLK_ASSESSOR_HOMEPAGE,
            "pso_search": _POLK_PSO_PORTAL,
            "polk_maps": _POLK_MAPS_PORTAL,
            "taxlot_rest": f"{_POLK_TAXLOT_REST_BASE}/query?where=TaxlotTemp_ORTaxlot%3D%27{row['taxlot']}%27&outFields=*&f=json",
        },
        "in_flight": [
            "Real-market valuation (RMV land/improvement/total) — Polk's public MapServer exposes only assessed values. RMV chain requires the Property Search Online (PSO) Blazor app, which is not URL-deep-linkable by account_id; queued behind a per-account fetcher.",
            "Building details (year built, sqft, bedrooms, baths) — not in the public MapServer source; reachable via PSO but requires a Blazor session scrape. Queued.",
            "Zoning — Polk publishes zoning on a separate MapServer not yet ingested into a polk_zoning table. Queued behind L1+L2 ship.",
            "Sale price for the latest instrument — not in the MapServer columns. Sale chain lives in PCMAPS.DBO.V_REAL_SALES (relationshipId 0 on the Taxlots layer); queryable on demand but queued as a follow-up.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_polk_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Polk deed / recorded-instrument snapshot for a taxlot.

    Pass-through over parcels_polk L1 inline data (no external HTTP). Returns
    the MOST-RECENT instrument-of-record on the parcel (number, type, year/
    month; source ships no day, no recorded sale price), the current owner of
    record, and a deep link to the Polk maps portal where the user can search
    the recording chain visually.

    What's IN FLIGHT:
      - Multi-instrument deed chain — only the latest instrument is in L1
        (one row per parcel). Polk Clerk's public recordings portal URL
        is not yet located (the www.polkcountyor.gov/clerk page 404s).
        Surfacing the maps portal + assessor home until a direct clerk
        URL is republished.
      - Recorded sale price — not exposed by the public MapServer. Sale
        chain lives in PCMAPS.DBO.V_REAL_SALES (relationshipId 0 on the
        Taxlots layer); queryable on demand but queued as a follow-up.

    Returns: {found, taxlot, county_fips, latest_instrument, owner_of_record,
              deep_links{polk_maps, assessor_home}, in_flight[], fetched_at,
              source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _polk_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_polk", "taxlot": taxlot}

    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_polk L1 inline (Polk County AcctTable last-instrument snapshot) + Polk maps portal deep link",
        "latest_instrument": {
            "instrument_id": row["inst_id"] or None,
            "type": row["inst_type"] or None,
            "year": int(row["inst_year"]) if row["inst_year"] else None,
            "month": int(row["inst_month"]) if row["inst_month"] else None,
            "date_iso": _polk_deed_date(row),
            "sale_price": None,
        } if has_instrument else None,
        "owner_of_record": {
            "primary": row["owner_line1"],
            "agent": row["agent_name"],
            "in_care_of": row["in_care_of"],
            "mailing_address_1": row["mail_add1"],
            "mailing_address_2": row["mail_add2"],
            "mailing_csz": _polk_mailing_csz(row),
        },
        "deep_links": {
            "digital_research_room": _POLK_DIGITAL_RESEARCH_ROOM,
            "maps_and_records_hub": _POLK_CLERK_HOMEPAGE,
            "assessor_home": _POLK_ASSESSOR_HOMEPAGE,
            "polk_maps": _POLK_MAPS_PORTAL,
        },
        "in_flight": [
            "Multi-instrument deed chain — L1 carries only the most-recent instrument. The Polk Clerk Digital Research Room (apps2.co.polk.or.us/DigitalResearchRoom) holds the 1984-present recordings index, but isn't URL-deep-linkable to a specific parcel; surfacing it as a user-driven search until a per-parcel scrape is built.",
            "Recorded sale price — not exposed by the public MapServer. Sale chain lives in PCMAPS.DBO.V_REAL_SALES (relationshipId 0 on the Taxlots layer); queryable on demand but queued as a follow-up.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_polk_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Polk (FIPS 053).

    Polk is Tier 2 Major. Per the emerging pattern (Tier 1 Flagships run
    county-direct Accela tenants; Tier 2/3/4 ride state Accela), Polk
    jurisdictions — Dallas, Independence, Monmouth, Falls City, Willamina,
    and unincorporated Polk — route through Oregon's state ePermitting
    (aca-oregon.accela.com /oregon module). Polk FIPS '053' is in
    _OREGON_EPERMITTING_FIPS so this delegates cleanly.
    """
    result = fetch_county_permits(taxlot, "053", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Polk County (Dallas / Independence / Monmouth / Falls City / Willamina / "
            "unincorporated — Oregon state Accela; Tier 2 reuse pattern)"
        )
    return result
