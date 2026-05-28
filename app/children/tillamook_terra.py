"""
TILLAMOOK County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, Tier 3 Standard,
coastal cluster #2 of 3: Clatsop / Tillamook / Lincoln).

Lives in its own module (linn/polk/yamhill/josephine/clatsop precedent) so the
parallel-Yindo-session edits on orb_db.py don't collide. Re-exported from
orb_db.py via `from .tillamook_terra import fetch_tillamook_*`.

Reality of the Tillamook integration:

  1. parcels_tillamook L1 comes from a hosted ArcGIS Online layer
     (services.arcgis.com/uUvqNMGPm7axC2dD/.../Tilllamook_Taxlot_Owners — note the
     triple-L typo in the real service name — same State-of-Oregon AGO org that
     hosts Yamhill_County_Landowners). It ships the FULL Yamhill/ODF family schema
     PLUS the richest extras TERRA has seen on an ODF-family feed:

       * VALUATION INLINE and LIVE — ASSESSVAL (assessed total, 90% populated) +
         LANDVALUE + IMPVALUE. This is NOT in flight (contrast Yamhill / Jefferson
         which ship no valuation, Clatsop whose valuation is token-gated). Surface
         it as the headline number.
       * Account / tax status (ACCTSTATUS / TAXSTATUS).
       * Tax routing (MA / SA / NH).

     Owner (OWNERLINE1/2/3) is 95% populated, situs (SITEADDNAM) 63%. So
     fetch_tillamook_assessment + fetch_tillamook_records are BOTH SQL
     pass-throughs over L1 inline — no scraper, no viewstate gating, no per-call
     HTTP.

  2. What L1 does NOT carry (flagged IN FLIGHT honestly, never invented):
       Sale price  — INSTYEAR / INSTMONTH / INSTID / INSTTYPE inline (most-recent
                     transfer event), but recorded sale PRICE is not exposed
                     (same as Yamhill).
       Multi-
       instrument
       chain       — only the most-recent instrument is inline.
       RMV         — the feed ships assessed (ASSESSVAL) + land + improvement; a
                     distinct real-market-value figure is not present. Assessed is
                     the surfaced valuation.
       Zoning      — no Tillamook zoning service located/ingested yet
                     (Jackson-pattern follow-up). PRPCLSDSC carries the use class.
       Building
       detail      — year built / sqft / bed / bath not in source.

  3. Deep links — the Tillamook county website (www.tillamookcounty.gov) WAF-blocks
     datacenter IPs (403 to every path from the droplet), and the county-domain
     GIS hosts have no DNS from here. So the verified-reachable surfaces are the
     hosted ArcGIS taxlot REST deep link + the county homepage (which a human
     broker's browser loads fine). The per-parcel assessor / clerk deep-link
     parameterization is IN FLIGHT — the broker navigates from the county home to
     Assessment & Taxation / County Clerk Records.

  4. Permits — Tillamook is Tier 3 Standard. Per the established pattern (Tier 1
     Flagships run county-direct Accela tenants; Tier 2/3/4 ride state Accela),
     all Tillamook jurisdictions — Tillamook (city), Garibaldi, Bay City,
     Rockaway Beach, Manzanita, Nehalem, Wheeler, Pacific City, and
     unincorporated Tillamook — default-route through Oregon's state ePermitting
     (aca-oregon.accela.com /oregon). FIPS '057' is added to
     _OREGON_EPERMITTING_FIPS so fetch_tillamook_permits is a clean wrapper.

  Coastal STR + dairy/ag dual angle: Tillamook is the north-coast tourism market
  (Pacific City / Rockaway / Manzanita beaches) layered over the dairy-belt
  farmland — assessed valuation inline makes it the strongest L2 of the coastal
  cluster for the broker audience.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


_TILLAMOOK_COUNTY_HOME = "https://www.tillamookcounty.gov/"
_TILLAMOOK_TAXLOT_REST_BASE = (
    "https://services.arcgis.com/uUvqNMGPm7axC2dD/arcgis/rest/services/"
    "Tilllamook_Taxlot_Owners/FeatureServer/0"
)


def _tillamook_l1_row(taxlot: str):
    """Fetch the L1 row for a Tillamook taxlot. Resolves on ORTaxlot (29-char
    primary natural key) first, then MapTaxlot, then PRIMACCNUM for broker
    convenience."""
    if not taxlot:
        return None
    canon = taxlot.strip().upper()
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT id, fid, taxlot, county_fips,
                          maptaxlot, ortaxlot, map_taxlot_short,
                          map_number, ormapnum, special_int,
                          plss_county, plss_town, plss_town_part, plss_town_dir,
                          plss_range, plss_range_part, plss_range_dir,
                          plss_section, plss_qtr, plss_qtr_qtr, plss_anomaly,
                          prim_acc_num, si_map_tax, ref_link,
                          owner_line1, owner_line2, owner_line3, agent_name,
                          mail_add1, mail_add2, mail_city, mail_state, mail_zip, mail_country,
                          site_add_nam, site_add_cty, site_zip,
                          inst_year, inst_month, inst_id, inst_type,
                          dwelling, prp_class, prp_cls_desc,
                          ma, sa, nh,
                          assessed_value, land_value, improvement_value,
                          acct_status, tax_status,
                          taxlot_acres, map_acres, taxlot_feet,
                          ROUND((ST_Area(geom::geography) / 4046.8564224)::numeric, 3) AS acres,
                          ingested_at, last_modified
                   FROM parcels_tillamook
                   WHERE taxlot = %s OR maptaxlot = %s OR prim_acc_num = %s
                   LIMIT 1""",
                (canon, canon, canon),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[tillamook_l1_row] DB error: {e}")
        return None


def _tillamook_owner_names(row) -> list[str]:
    out = []
    for k in ("owner_line1", "owner_line2", "owner_line3"):
        v = (row[k] or "").strip()
        if v:
            out.append(v)
    return out


def _tillamook_mailing_csz(row) -> str | None:
    city = (row["mail_city"] or "").strip() or None
    state = (row["mail_state"] or "").strip() or None
    zipc = (row["mail_zip"] or "").strip() or None
    if not (city or state or zipc):
        return None
    left = ", ".join(p for p in (city, state) if p) or None
    return f"{left}  {zipc}".strip() if left and zipc else (left or zipc)


def _tillamook_situs_address(row) -> str | None:
    nam = (row["site_add_nam"] or "").rstrip(", ").strip() or None
    cty = (row["site_add_cty"] or "").strip() or None
    if not nam:
        return None
    return f"{nam}, {cty}" if cty else nam


def _tillamook_section_id(row) -> str | None:
    t, td = row["plss_town"], (row["plss_town_dir"] or "").strip()
    r, rd = row["plss_range"], (row["plss_range_dir"] or "").strip()
    s = row["plss_section"]
    q, qq = (row["plss_qtr"] or "").strip(), (row["plss_qtr_qtr"] or "").strip()
    if not (t and r and s):
        return None
    sec = f"{int(t)}{td}-{int(r)}{rd}-{int(s)}"
    if q:
        sec += q + qq
    return sec


def _tillamook_deed_date(row) -> str | None:
    y, m = row["inst_year"], row["inst_month"]
    if not y:
        return None
    if m and 1 <= int(m) <= 12:
        return f"{int(y):04d}-{int(m):02d}-01"
    return f"{int(y):04d}-01-01"


def fetch_tillamook_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Tillamook assessment + ownership + VALUATION snapshot for a taxlot.

    Pass-through over parcels_tillamook L1 inline (no external HTTP). Returns
    up-to-three owner lines / mailing block / situs / account + tax status /
    property class / PLSS-derived section / acreage / recording snapshot AND
    LIVE valuation (assessed total + land + improvement) — Tillamook is the
    richest ODF-family L2 in TERRA because valuation ships inline.

    IN FLIGHT — honestly flagged, never invented:
      - RMV real-market value — the feed ships ASSESSED (ASSESSVAL) + land +
        improvement; a distinct RMV figure is not present. Assessed is the
        surfaced valuation.
      - Building details (year built / sqft / bed / bath) — not in source.
      - Zoning — no Tillamook zoning service ingested yet (Jackson-pattern
        follow-up); PRPCLSDSC carries the use class.
      - Per-parcel assessor deep link — county site WAF-blocks automated
        capture; broker navigates from the county home to Assessment & Taxation.

    Returns: {found, taxlot, county_fips, snapshot{owner, situs, account,
              section, acreage, valuation, recording_snapshot, classification},
              deep_links, in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _tillamook_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_tillamook", "taxlot": canon}

    owners = _tillamook_owner_names(row)
    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_tillamook L1 inline (Tillamook Taxlot Owners hosted FeatureServer — valuation inline) + Tillamook County deep links",
        "snapshot": {
            "owner": {
                "primary": owners[0] if owners else None,
                "all_owners": owners,
                "owner_count": len(owners),
                "owner_line_1": row["owner_line1"],
                "owner_line_2": row["owner_line2"],
                "owner_line_3": row["owner_line3"],
                "mailing_address_1": row["mail_add1"],
                "mailing_address_2": row["mail_add2"],
                "mailing_csz": _tillamook_mailing_csz(row),
                "mailing_country": row["mail_country"],
            },
            "situs": {
                "address": _tillamook_situs_address(row),
                "street": (row["site_add_nam"] or "").rstrip(", ").strip() or None,
                "city": row["site_add_cty"],
                "zip": row["site_zip"],
            },
            "account": {
                "prim_acc_num": row["prim_acc_num"],
                "si_map_tax": row["si_map_tax"],
                "account_status": row["acct_status"],
                "tax_status": row["tax_status"],
                "maintenance_area": row["ma"],
                "special_assessment": row["sa"],
                "neighborhood": row["nh"],
                "map_number": row["map_number"],
                "ormapnum": row["ormapnum"],
                "map_taxlot_short": row["map_taxlot_short"],
            },
            "classification": {
                "dwelling": row["dwelling"],
                "prop_class": row["prp_class"] or None,
                "prop_class_desc": row["prp_cls_desc"] or None,
            },
            "section": {
                "section_id": _tillamook_section_id(row),
                "township": row["plss_town"],
                "town_dir": row["plss_town_dir"],
                "range": row["plss_range"],
                "range_dir": row["plss_range_dir"],
                "section_number": row["plss_section"],
                "qtr": row["plss_qtr"],
                "qtr_qtr": row["plss_qtr_qtr"],
            },
            "acreage": {
                "taxlot_acres": float(row["taxlot_acres"]) if row["taxlot_acres"] is not None else None,
                "map_acres": float(row["map_acres"]) if row["map_acres"] is not None else None,
                "geodesic_acres": float(row["acres"]) if row["acres"] is not None else None,
                "taxlot_feet": row["taxlot_feet"],
            },
            "valuation": {
                "assessed_total": float(row["assessed_value"]) if row["assessed_value"] is not None else None,
                "land_value": float(row["land_value"]) if row["land_value"] is not None else None,
                "improvement_value": float(row["improvement_value"]) if row["improvement_value"] is not None else None,
                "rmv_total": None,
                "source": "inline parcels_tillamook ASSESSVAL / LANDVALUE / IMPVALUE (LIVE — not in flight)",
            },
            "recording_snapshot": {
                "instrument_id": row["inst_id"] or None,
                "type": row["inst_type"] or None,
                "year": int(row["inst_year"]) if row["inst_year"] else None,
                "month": int(row["inst_month"]) if row["inst_month"] else None,
                "date_iso": _tillamook_deed_date(row),
            } if has_instrument else None,
        },
        "deep_links": {
            "county_home": _TILLAMOOK_COUNTY_HOME,
            "taxlot_rest": (
                f"{_TILLAMOOK_TAXLOT_REST_BASE}/query?"
                f"where=ORTaxlot%3D%27{row['taxlot']}%27&outFields=*&f=json"
            ),
        },
        "in_flight": [
            "RMV real-market value — the feed ships ASSESSED valuation (ASSESSVAL) + land + improvement inline (surfaced LIVE); a distinct RMV figure is not in source. Assessed is the headline number.",
            "Building details (year built / sqft / bedrooms / baths) — not exposed by the source feed.",
            "Recorded sale PRICE — INSTID / INSTTYPE / INSTYEAR / INSTMONTH inline (most-recent transfer), but the recorded price is not in source (same as Yamhill).",
            "Zoning — no Tillamook zoning service located/ingested into a tillamook_zoning table yet (Jackson-pattern follow-up). PRPCLSDSC carries the use class until then.",
            "Per-parcel assessor / clerk deep link — the Tillamook county website WAF-blocks datacenter IPs (403). Broker navigates from the county home to Assessment & Taxation; the verified taxlot_rest deep link returns the raw record.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_tillamook_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Tillamook recorded-document snapshot for a taxlot.

    Pass-through over parcels_tillamook L1 inline. Returns the MOST-RECENT
    instrument-of-record (number, type, year/month; source ships no day, no
    sale price), the current owners of record, plus deep links. Multi-instrument
    deed chain + sale price are IN FLIGHT (only the latest instrument is in L1;
    county recording surface is WAF-blocked from the droplet). Honest framing per
    DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _tillamook_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_tillamook", "taxlot": canon}

    owners = _tillamook_owner_names(row)
    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_tillamook L1 inline (last-instrument snapshot) + Tillamook County deep links",
        "latest_instrument": {
            "instrument_id": row["inst_id"] or None,
            "type": row["inst_type"] or None,
            "year": int(row["inst_year"]) if row["inst_year"] else None,
            "month": int(row["inst_month"]) if row["inst_month"] else None,
            "date_iso": _tillamook_deed_date(row),
            "sale_price": None,
        } if has_instrument else None,
        "owners_of_record": {
            "primary": owners[0] if owners else None,
            "all_owners": owners,
            "owner_count": len(owners),
            "mailing_address_1": row["mail_add1"],
            "mailing_address_2": row["mail_add2"],
            "mailing_csz": _tillamook_mailing_csz(row),
        },
        "deep_links": {
            "county_home": _TILLAMOOK_COUNTY_HOME,
            "taxlot_rest": (
                f"{_TILLAMOOK_TAXLOT_REST_BASE}/query?"
                f"where=ORTaxlot%3D%27{row['taxlot']}%27&outFields=*&f=json"
            ),
        },
        "in_flight": [
            "Multi-instrument deed chain — L1 carries only the most-recent instrument. The Tillamook County Clerk recording surface is WAF-blocked from the droplet; broker navigates from the county home to County Clerk Records.",
            "Recorded sale price — not exposed by the source feed.",
            "Instrument type — present for the most-recent transfer but sometimes blank in INSTTYPE; surfacing what's there, null when missing.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_tillamook_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Tillamook (FIPS 057).

    Tillamook is Tier 3 Standard. Per the established pattern (Tier 1 Flagships
    run county-direct Accela tenants; Tier 2/3/4 ride state Accela), all Tillamook
    jurisdictions — Tillamook (city), Garibaldi, Bay City, Rockaway Beach,
    Manzanita, Nehalem, Wheeler, Pacific City, and unincorporated Tillamook —
    default-route through Oregon's state ePermitting (aca-oregon.accela.com
    /oregon). Tillamook FIPS '057' is in _OREGON_EPERMITTING_FIPS so this
    delegates cleanly. No dedicated Tillamook permits GIS FeatureServer exists, so
    the state-Accela deep link is the canonical surface. Honest IN FLIGHT framing
    per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    result = fetch_county_permits(taxlot, "057", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Tillamook County (Tillamook / Garibaldi / Bay City / Rockaway Beach / "
            "Manzanita / Nehalem / Wheeler / Pacific City / unincorporated — Oregon "
            "state Accela; Tier 3 reuse pattern)"
        )
    return result
