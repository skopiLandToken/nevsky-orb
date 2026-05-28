"""
LINCOLN County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, Tier 3 Standard,
coastal cluster #3 of 3: Clatsop / Tillamook / Lincoln).

Lives in its own module (linn/polk/yamhill/josephine/clatsop/tillamook precedent),
re-exported from orb_db.py via `from .lincoln_terra import fetch_lincoln_*`.

Reality of the Lincoln integration — the THIN / WASHINGTON-PATTERN ship:

  1. Lincoln County runs NO ArcGIS Server. Its public map is GeoMOOSE backed by
     UMN MapServer/WFS (a_assessment.map) — not ArcGIS-queryable. The owner /
     situs / valuation attributes live ONLY behind that WFS. The ODF statewide
     MapServer has query disabled. So the full-county L1 is the hosted AGO ORMAP
     cadastre layer "taxlot21" (services3.arcgis.com/MiIvpOH5WqZfaAvr/...), which
     ships ONLY polygon attributes: taxlot identifiers, PLSS, map suffix/class,
     acreage (TaxlotAcre + TaxlotFeet), REFLink. NO owner / situs / account /
     valuation / instrument.

     This mirrors the Washington County L1 ship exactly (geometry + taxlot +
     acreage; ownership resolved off-source). fetch_lincoln_assessment surfaces
     the cadastre inline and flags owner/situs/valuation IN FLIGHT honestly —
     it does NOT fabricate.

  2. The fetchers are written to surface owner / situs / instrument / valuation
     WHEN PRESENT (the parcels_lincoln columns exist, currently NULL). So the
     queued Newport-UGB enrichment lights them up automatically with no code
     change:

       ENRICHMENT (queued follow-up):
         services.arcgis.com/Nse9mKSgB2fD47Hg/.../Taxlot_2023_Newport_UGB/
         FeatureServer/0 carries OwnerLine1/2/3 + MailAdd + situsall + PrimAccNum
         + PrpClass/PrpClsDsc + InstYear/Month/ID/Type for the ~8,373 Newport-UGB
         parcels (the flagship coastal market). An ORTaxlot-keyed UPDATE backfills
         owner/situs/instrument for Newport. County-wide owner/situs is a larger
         GeoMOOSE/WFS GML-parse follow-up.

  3. Deep links — co.lincoln.or.us IS reachable (CivicPlus). The Property
     Information Search page (/1000) is the broker hand-off for owner / situs /
     valuation until the enrichment lands.

  4. Permits — Lincoln is Tier 3 Standard. Per the established pattern (Tier 1
     Flagships run county-direct Accela tenants; Tier 2/3/4 ride state Accela),
     all Lincoln jurisdictions — Newport, Lincoln City, Toledo, Waldport, Depoe
     Bay, Yachats, Siletz, and unincorporated Lincoln — default-route through
     Oregon's state ePermitting (aca-oregon.accela.com /oregon). FIPS '041' is
     added to _OREGON_EPERMITTING_FIPS so fetch_lincoln_permits is a clean wrapper.

  Coastal STR angle: Lincoln is the central-coast tourism market (Newport /
  Lincoln City / Depoe Bay / Yachats). The county Transient Lodging Tax portal
  (/1240) is surfaced for the short-term-rental broker audience.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


_LINCOLN_PROPERTY_SEARCH = "https://www.co.lincoln.or.us/1000/Property-Information-Search-Page"
_LINCOLN_ASSESSOR = "https://www.co.lincoln.or.us/157/Assessors-Office"
_LINCOLN_MAPS = "https://www.co.lincoln.or.us/161/Maps"
_LINCOLN_RECORDING = "https://www.co.lincoln.or.us/177/Document-Recording-Fees"
_LINCOLN_SURVEYOR = "https://www.co.lincoln.or.us/354/Surveyors-Office"
_LINCOLN_TRANSIENT_LODGING = "https://www.co.lincoln.or.us/1240/Transient-Lodging-Tax-Online-Payment-Por"
_LINCOLN_TAXLOT_REST_BASE = (
    "https://services3.arcgis.com/MiIvpOH5WqZfaAvr/arcgis/rest/services/"
    "taxlot21/FeatureServer/0"
)
_LINCOLN_NEWPORT_UGB_REST = (
    "https://services.arcgis.com/Nse9mKSgB2fD47Hg/arcgis/rest/services/"
    "Taxlot_2023_Newport_UGB/FeatureServer/0"
)


def _lincoln_l1_row(taxlot: str):
    """Fetch the L1 row for a Lincoln taxlot. Resolves on ORTaxlot (29-char
    primary natural key) first, then MapTaxlot, then PRIMACCNUM (populated only
    after the Newport-UGB enrichment). Acreage prefers source TaxlotAcre, falling
    back to geodesic for ROAD / zero-acre rows."""
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
                          map_suf_type, map_suf_num, map_class, map_rel_code, relia_code, ref_link,
                          prim_acc_num, owner_line1, owner_line2, owner_line3,
                          mail_add1, mail_add2, mail_city, mail_state, mail_zip,
                          site_add_nam, site_add_cty, site_zip,
                          inst_year, inst_month, inst_id, inst_type,
                          dwelling, prp_class, prp_cls_desc,
                          assessed_value, land_value, improvement_value,
                          taxlot_acres, taxlot_feet,
                          ROUND(COALESCE(NULLIF(taxlot_acres, 0),
                                         (ST_Area(geom::geography) / 4046.8564224))::numeric, 3) AS acres,
                          ingested_at, last_modified
                   FROM parcels_lincoln
                   WHERE taxlot = %s OR maptaxlot = %s OR prim_acc_num = %s
                   LIMIT 1""",
                (canon, canon, canon),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[lincoln_l1_row] DB error: {e}")
        return None


def _lincoln_owner_names(row) -> list[str]:
    out = []
    for k in ("owner_line1", "owner_line2", "owner_line3"):
        v = (row[k] or "").strip()
        if v:
            out.append(v)
    return out


def _lincoln_situs_address(row) -> str | None:
    nam = (row["site_add_nam"] or "").rstrip(", ").strip() or None
    cty = (row["site_add_cty"] or "").strip() or None
    if not nam:
        return None
    return f"{nam}, {cty}" if cty else nam


def _lincoln_section_id(row) -> str | None:
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


def _lincoln_deed_date(row) -> str | None:
    y, m = row["inst_year"], row["inst_month"]
    if not y:
        return None
    if m and 1 <= int(m) <= 12:
        return f"{int(y):04d}-{int(m):02d}-01"
    return f"{int(y):04d}-01-01"


def fetch_lincoln_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Lincoln assessment + parcel snapshot for a taxlot (THIN / Washington-pattern).

    Pass-through over parcels_lincoln L1 inline (no external HTTP). The full-county
    cadastre source (taxlot21) ships ONLY polygon attributes, so this surfaces
    taxlot identifiers / PLSS-derived section / acreage / map number / REFLink
    inline, and flags owner / situs / valuation IN FLIGHT honestly — it does NOT
    fabricate. Owner / situs / instrument / valuation fields are surfaced WHEN
    PRESENT (NULL county-wide on first ingest; populated for the Newport-UGB
    subset after the queued enrichment lands).

    IN FLIGHT — honestly flagged, never invented:
      - Owner / mailing / situs county-wide — NOT in the taxlot21 cadastre feed.
        Lincoln runs no ArcGIS Server; owner/situs lives behind the county's
        GeoMOOSE/WFS (a_assessment.map). Broker pulls owner/situs/valuation from
        the Property Information Search page (co.lincoln.or.us/1000) until the
        Newport-UGB enrichment + WFS follow-up land.
      - Valuation (assessed / RMV) — same: behind Property Information Search.
      - Building details + recorded instrument — same.

    Returns: {found, taxlot, county_fips, snapshot{parcel, section, acreage,
              owner, situs, valuation}, deep_links, in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _lincoln_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_lincoln", "taxlot": canon}

    owners = _lincoln_owner_names(row)
    has_owner = bool(owners)
    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_lincoln L1 inline (Lincoln taxlot21 ORMAP cadastre — thin/Washington-pattern) + Lincoln County deep links",
        "snapshot": {
            "parcel": {
                "taxlot": row["taxlot"],
                "maptaxlot": row["maptaxlot"],
                "map_taxlot_short": row["map_taxlot_short"],
                "map_number": row["map_number"],
                "ormapnum": row["ormapnum"],
                "special_int": row["special_int"],
                "map_class": row["map_class"],
                "ref_link": row["ref_link"] or None,
            },
            "section": {
                "section_id": _lincoln_section_id(row),
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
                "geodesic_acres": float(row["acres"]) if row["acres"] is not None else None,
                "taxlot_feet": row["taxlot_feet"],
            },
            # Owner / situs / valuation surface WHEN PRESENT (Newport-UGB enrichment).
            "owner": {
                "primary": owners[0] if owners else None,
                "all_owners": owners,
                "owner_count": len(owners),
                "mailing_address_1": row["mail_add1"],
                "mailing_city": row["mail_city"],
                "mailing_state": row["mail_state"],
                "mailing_zip": row["mail_zip"],
                "status": "live" if has_owner else "in_flight (not in county-wide cadastre feed)",
            },
            "situs": {
                "address": _lincoln_situs_address(row),
                "city": row["site_add_cty"],
                "status": "live" if row["site_add_nam"] else "in_flight (not in county-wide cadastre feed)",
            },
            "classification": {
                "prop_class": row["prp_class"] or None,
                "prop_class_desc": row["prp_cls_desc"] or None,
                "dwelling": row["dwelling"],
            },
            "valuation": {
                "assessed_total": float(row["assessed_value"]) if row["assessed_value"] is not None else None,
                "land_value": float(row["land_value"]) if row["land_value"] is not None else None,
                "improvement_value": float(row["improvement_value"]) if row["improvement_value"] is not None else None,
                "status": "in_flight (behind Property Information Search)",
            },
            "recording_snapshot": {
                "instrument_id": row["inst_id"] or None,
                "type": row["inst_type"] or None,
                "year": int(row["inst_year"]) if row["inst_year"] else None,
                "month": int(row["inst_month"]) if row["inst_month"] else None,
                "date_iso": _lincoln_deed_date(row),
            } if has_instrument else None,
        },
        "deep_links": {
            "property_information_search": _LINCOLN_PROPERTY_SEARCH,
            "assessor_office": _LINCOLN_ASSESSOR,
            "maps": _LINCOLN_MAPS,
            "transient_lodging_tax": _LINCOLN_TRANSIENT_LODGING,
            "taxlot_rest": (
                f"{_LINCOLN_TAXLOT_REST_BASE}/query?"
                f"where=ORTaxlot%3D%27{row['taxlot']}%27&outFields=*&f=json"
            ),
            "newport_ugb_rich_rest": _LINCOLN_NEWPORT_UGB_REST,
        },
        "in_flight": [
            "Owner / mailing / situs (county-wide) — NOT in the Lincoln taxlot21 cadastre feed. Lincoln County runs no ArcGIS Server; owner/situs lives behind the county GeoMOOSE/WFS (a_assessment.map). Broker pulls owner/situs from the Property Information Search page (co.lincoln.or.us/1000). ENRICHMENT QUEUED: the Newport-UGB rich layer backfills owner/situs/instrument for the ~8,373 Newport-UGB parcels by ORTaxlot; county-wide is a WFS GML-parse follow-up.",
            "Valuation (assessed / RMV, land / improvement) — not in the cadastre feed. Behind Property Information Search / the Newport-UGB layer.",
            "Building details (year built / sqft / bed / bath) — not in source.",
            "Recorded instrument / sale — not in the cadastre feed (the Newport-UGB layer carries the latest instrument for its subset). Document Recording surface at co.lincoln.or.us/177.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_lincoln_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Lincoln recorded-document snapshot for a taxlot (THIN / Washington-pattern).

    The full-county cadastre feed carries NO recorded-instrument data, so the
    recording chain is IN FLIGHT — surfaced via the County Document Recording deep
    link. Owner-of-record and the latest instrument surface WHEN PRESENT (populated
    for the Newport-UGB subset after the queued enrichment). Honest framing per
    DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _lincoln_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_lincoln", "taxlot": canon}

    owners = _lincoln_owner_names(row)
    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_lincoln L1 inline (thin cadastre) + Lincoln County recording deep links",
        "latest_instrument": {
            "instrument_id": row["inst_id"] or None,
            "type": row["inst_type"] or None,
            "year": int(row["inst_year"]) if row["inst_year"] else None,
            "month": int(row["inst_month"]) if row["inst_month"] else None,
            "date_iso": _lincoln_deed_date(row),
            "sale_price": None,
        } if has_instrument else None,
        "owners_of_record": {
            "primary": owners[0] if owners else None,
            "all_owners": owners,
            "owner_count": len(owners),
            "status": "live" if owners else "in_flight (not in county-wide cadastre feed)",
        },
        "deep_links": {
            "document_recording": _LINCOLN_RECORDING,
            "property_information_search": _LINCOLN_PROPERTY_SEARCH,
            "assessor_office": _LINCOLN_ASSESSOR,
        },
        "in_flight": [
            "Recorded-instrument chain of title (deed history, instrument numbers, sale price) — NOT in the Lincoln taxlot21 cadastre feed. The County Document Recording surface (co.lincoln.or.us/177) and Property Information Search are the broker hand-off. ENRICHMENT QUEUED: the Newport-UGB rich layer carries the most-recent instrument for its subset.",
            "Owner-of-record county-wide — behind the GeoMOOSE/WFS; surfaced via Property Information Search until enrichment lands.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_lincoln_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Lincoln (FIPS 041).

    Lincoln is Tier 3 Standard. Per the established pattern (Tier 1 Flagships run
    county-direct Accela tenants; Tier 2/3/4 ride state Accela), all Lincoln
    jurisdictions — Newport, Lincoln City, Toledo, Waldport, Depoe Bay, Yachats,
    Siletz, and unincorporated Lincoln — default-route through Oregon's state
    ePermitting (aca-oregon.accela.com /oregon). Lincoln FIPS '041' is in
    _OREGON_EPERMITTING_FIPS so this delegates cleanly. No dedicated Lincoln
    permits GIS FeatureServer exists, so the state-Accela deep link is the
    canonical surface. Honest IN FLIGHT framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    result = fetch_county_permits(taxlot, "041", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Lincoln County (Newport / Lincoln City / Toledo / Waldport / Depoe Bay / "
            "Yachats / Siletz / unincorporated — Oregon state Accela; Tier 3 reuse pattern)"
        )
    return result
