"""
YAMHILL County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, county #11).

Lives in its own module (Linn / Polk precedent) so the parallel-Yindo-session
edits on orb_db.py don't collide. Re-exported from orb_db.py via
`from .yamhill_terra import fetch_yamhill_*` so the public contract matches
every other county (`from app.children.orb_db import fetch_yamhill_*`).

Reality of the Yamhill integration:

  1. parcels_yamhill L1 is Polk-rich: the Yamhill_County_Landowners FeatureServer
     (services.arcgis.com/uUvqNMGPm7axC2dD/.../Yamhill_County_Landowners/
     FeatureServer/0) ships ORMAP polygon attrs JOINED to assessor account attrs
     in one row. Owner (up to OWNERLINE1/2/3), mailing block, situs
     (SITEADDNAM/CTY/ZIP), last instrument (year/month/id/type), and full PLSS
     (County / Town / Range / SecNumber / Qtr / QtrQtr) are inline. So
     fetch_yamhill_assessment and fetch_yamhill_records are SQL pass-throughs
     over L1 inline data (Polk pattern) — NO scraper, NO viewstate gating, NO
     HTTP per request.

     Owner coverage measured at ingest: 95.3% (40,819 / 42,852). Of those with
     owners, 43.1% have OWNERLINE2 populated (multi-owner / co-trustee) and
     10.5% have OWNERLINE3 — so the trust / family-LLC vineyard parcels surface
     all owners without an explosion table (Benton's STRING_AGG problem) or a
     scraper (Linn's pub_sales blocker).

  2. What Yamhill's source publishes-but-leaves-empty (upstream gap, NOT a
     parser bug — flagged IN FLIGHT for the downstream user):

       PRPCLASS    — schema field exists, value is "00" or empty for 100% of rows.
                     The vineyard demo angle therefore CANNOT key off prop class
                     in L1. It surfaces via owner_line1 keyword scan instead
                     (RESONANCE WINES LLC / WILLAMETTE VALLEY VINEYARDS INC /
                     ELTON VINEYARDS LLC / PARDIS WINERY LLC / VF WINE COMPANY
                     LLC / etc — 100+ wine-business LLCs verified in L1).
       PRPCLSDSC   — same: schema present, empty for 100% of rows. EFU / AF /
                     FARMSITE / FOREST designations not in this feed.
       REFLink     — schema present, empty for 100% of rows. No per-parcel
                     deep link surfaced by the source despite the field name
                     implying one. fetch_yamhill_assessment falls back to the
                     assessor homepage + account-number search hint.
       AGENTNAME   — empty for 100% of rows (display field, never populated).

     These are all flagged IN FLIGHT honestly. A future Yamhill zoning service
     ingest is queued (Jackson pattern) — not blocking ship.

  3. What L1 does NOT carry inline (vs Polk):
       Valuation  — no AstLndVal / AstImpVal / AstValue. Polk has assessed
                    values; Yamhill's source does not. The broker pulls
                    valuation from the assessor portal via the account number.
                    Flagged IN FLIGHT.
       Building details (year built / sqft / bed / bath) — same gap.
       Sale price — not exposed. Recording instrument # / type / year are
                    inline (INSTID / INSTTYPE / INSTYEAR) so the most-recent
                    transfer event is surfaced; sale price is IN FLIGHT.

  4. Yamhill's official county subdomain (.or.us) is firewalled (403 from
     non-browser UA); the .gov subdomain (www.yamhillcounty.gov) is the
     stable deep-link surface. Assessor + Clerk home pages route there.

  5. Permits — Yamhill is Tier 2 Major. Per the emerging pattern (Tier 1
     Flagships run county-direct Accela tenants; Tier 2/3/4 ride state
     Accela), Yamhill jurisdictions — McMinnville, Newberg, Dundee, Carlton,
     Lafayette, Dayton, Sheridan, Willamina, Yamhill (city), Amity, and
     unincorporated Yamhill — default-route through Oregon state ePermitting
     (aca-oregon.accela.com /oregon). FIPS '071' is added to
     _OREGON_EPERMITTING_FIPS so fetch_yamhill_permits is a clean wrapper.

     McMinnville and Newberg are the two largest jurisdictions and MAY run
     their own city portals (briefed possibility, recon hit a 403 on
     mcminnvilleoregon.gov community development and a 404 on
     newbergoregon.gov /communitydevelopment). Until those portals are
     resolved, the state-Accela deep link is the canonical surface for ALL
     Yamhill jurisdictions; city-specific routing is flagged IN FLIGHT.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


_YAMHILL_ASSESSOR_HOMEPAGE = "https://www.yamhillcounty.gov/assessor"
_YAMHILL_CLERK_HOMEPAGE = "https://www.yamhillcounty.gov/clerk"
_YAMHILL_MAPS_PORTAL = (
    "https://www.arcgis.com/apps/mapviewer/index.html"
    "?layers=87ae6b162f074666928aaa1c2ee936a0"
)
_YAMHILL_TAXLOT_REST_BASE = (
    "https://services.arcgis.com/uUvqNMGPm7axC2dD/ArcGIS/rest/services/"
    "Yamhill_County_Landowners/FeatureServer/0"
)


def _yamhill_l1_row(taxlot: str):
    """Fetch the L1 row for a Yamhill taxlot.

    Resolves against ORTaxlot (29-char primary natural key) first, then
    against the human-friendly MapTaxlot variant. Returns dict or None.
    """
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, county_fips,
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
                          taxlot_acres, taxlot_feet,
                          ingested_at, last_modified
                   FROM parcels_yamhill
                   WHERE taxlot = %s OR maptaxlot = %s
                   LIMIT 1""",
                (taxlot, taxlot),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[yamhill_l1_row] DB error: {e}")
        return None


def _yamhill_owner_names(row) -> list[str]:
    """Combine OWNERLINE1/2/3 into a list of non-empty trimmed names."""
    out = []
    for k in ("owner_line1", "owner_line2", "owner_line3"):
        v = (row[k] or "").strip()
        if v:
            out.append(v)
    return out


def _yamhill_mailing_csz(row) -> str | None:
    city = (row["mail_city"] or "").strip() or None
    state = (row["mail_state"] or "").strip() or None
    zipc = (row["mail_zip"] or "").strip() or None
    if not (city or state or zipc):
        return None
    left = ", ".join(p for p in (city, state) if p) or None
    return f"{left}  {zipc}".strip() if left and zipc else (left or zipc)


def _yamhill_situs_address(row) -> str | None:
    """SITEADDNAM in Yamhill source carries the full street line with trailing
    comma (e.g. "535 NE 5TH ST,"). Strip the comma + space and pair with city.
    """
    nam = (row["site_add_nam"] or "").rstrip(", ").strip() or None
    cty = (row["site_add_cty"] or "").strip() or None
    if not nam:
        return None
    return f"{nam}, {cty}" if cty else nam


def _yamhill_section_id(row) -> str | None:
    """Compose human-readable section id from PLSS — e.g. "4S-4W-21BC"."""
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


def _yamhill_deed_date(row) -> str | None:
    """Synthesize an ISO date from inst_year + inst_month (source has no day)."""
    y, m = row["inst_year"], row["inst_month"]
    if not y:
        return None
    if m and 1 <= int(m) <= 12:
        return f"{int(y):04d}-{int(m):02d}-01"
    return f"{int(y):04d}-01-01"


def fetch_yamhill_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Yamhill assessment + ownership snapshot for a taxlot.

    Pass-through over parcels_yamhill L1 inline data (no external HTTP).
    Returns up-to-three owner lines / mailing block / situs / account /
    PLSS-derived section / acreage, plus a deep link to the Yamhill assessor
    portal where the user can resolve the per-account record visually.

    What's IN FLIGHT (upstream gaps in the public feed, flagged honestly):
      - Valuation (assessed land/improvement/total + RMV-family) — Yamhill's
        public bulk service exposes NO valuation fields (vs Polk which ships
        assessed values). The broker pulls valuation from the assessor portal
        via the account number.
      - Property class + description (PRPCLASS / PRPCLSDSC) — schema fields
        present but empty for 100% of rows in source. The vineyard / EFU /
        AF designation therefore can't key off prop class in L1; the demo
        angle surfaces via owner_line1 ("RESONANCE WINES LLC" /
        "WILLAMETTE VALLEY VINEYARDS INC" / etc — wine-LLC scan) and
        situs_city (AMITY / DUNDEE / DAYTON in Dundee Hills / Eola-Amity
        Hills AVAs).
      - Per-parcel REFLink — schema field present, empty for 100% of rows;
        deep links fall back to the assessor homepage with the account
        number to paste.
      - Building details (year built / sqft / bed / bath) — not in source.
      - Yamhill zoning (M-AF / EFU / RR-5 etc) — not yet ingested into a
        yamhill_zoning table; Jackson-pattern follow-up.

    Returns: {found, taxlot, county_fips, snapshot{owner,situs,account,
              section,acreage,recording_snapshot}, deep_links{assessor_home,
              clerk_home, taxlot_rest, maps_portal}, in_flight[], fetched_at,
              source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _yamhill_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_yamhill", "taxlot": taxlot}

    owners = _yamhill_owner_names(row)
    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_yamhill L1 inline (Yamhill County Landowners FeatureServer joined to assessor account attrs) + Yamhill County deep links",
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
                "mailing_csz": _yamhill_mailing_csz(row),
                "mailing_country": row["mail_country"],
            },
            "situs": {
                "address": _yamhill_situs_address(row),
                "street": (row["site_add_nam"] or "").rstrip(", ").strip() or None,
                "city": row["site_add_cty"],
                "zip": row["site_zip"],
            },
            "account": {
                "prim_acc_num": row["prim_acc_num"],
                "si_map_tax": row["si_map_tax"],
                "dwelling": row["dwelling"],
                "prop_class": row["prp_class"] or None,
                "prop_class_desc": row["prp_cls_desc"] or None,
            },
            "section": {
                "section_id": _yamhill_section_id(row),
                "township": row["plss_town"],
                "town_dir": row["plss_town_dir"],
                "range": row["plss_range"],
                "range_dir": row["plss_range_dir"],
                "section_number": row["plss_section"],
                "qtr": row["plss_qtr"],
                "qtr_qtr": row["plss_qtr_qtr"],
                "map_number": row["map_number"],
                "ormapnum": row["ormapnum"],
                "map_taxlot_short": row["map_taxlot_short"],
            },
            "acreage": {
                "taxlot_acres": float(row["taxlot_acres"]) if row["taxlot_acres"] is not None else None,
                "taxlot_feet": row["taxlot_feet"],
            },
            "recording_snapshot": {
                "instrument_id": row["inst_id"] or None,
                "type": row["inst_type"] or None,
                "year": int(row["inst_year"]) if row["inst_year"] else None,
                "month": int(row["inst_month"]) if row["inst_month"] else None,
                "date_iso": _yamhill_deed_date(row),
            } if has_instrument else None,
            "valuation": {
                "assessed_land": None,
                "assessed_improvement": None,
                "assessed_total": None,
                "rmv_land": None,
                "rmv_improvement": None,
                "rmv_total": None,
            },
        },
        "deep_links": {
            "assessor_home": _YAMHILL_ASSESSOR_HOMEPAGE,
            "clerk_home": _YAMHILL_CLERK_HOMEPAGE,
            "maps_portal": _YAMHILL_MAPS_PORTAL,
            "taxlot_rest": (
                f"{_YAMHILL_TAXLOT_REST_BASE}/query?"
                f"where=ORTaxlot%3D%27{row['taxlot']}%27&outFields=*&f=json"
            ),
        },
        "in_flight": [
            "Valuation (assessed land/improvement/total, RMV-family) — Yamhill's public Landowners feed exposes NO valuation fields. The broker pulls assessed + RMV from the per-account assessor portal (paste prim_acc_num into yamhillcounty.gov/assessor). Per-account scrape queued.",
            "Property class + description (PRPCLASS / PRPCLSDSC) — schema fields present but unpopulated for 100% of rows. Vineyard / EFU / AF detection surfaces via owner_line1 keyword scan + situs_city (AMITY / DUNDEE / DAYTON in Dundee Hills AVA) instead. Real zoning ingest queued (Jackson pattern).",
            "Per-parcel REFLink — schema field present, empty for 100% of rows. Deep link falls back to assessor homepage + account number.",
            "Building details (year built, sqft, bedrooms, baths) — not exposed by source. Behind the per-account portal.",
            "Yamhill zoning service — not yet located/ingested into a yamhill_zoning table. Queued behind L1+L2 ship.",
            "AGENTNAME (agent of record) — schema field present, empty for 100% of rows in this feed.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_yamhill_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Yamhill Clerk recorded-document snapshot for a taxlot.

    Pass-through over parcels_yamhill L1 inline data (no external HTTP).
    Returns the MOST-RECENT instrument-of-record on the parcel (number,
    type, year/month; source ships no day, no sale price), the current
    owners of record (up to three lines per source), and deep links to the
    Yamhill Clerk + assessor portals where the user can search the full
    recording chain visually.

    What's IN FLIGHT:
      - Multi-instrument deed chain — only the latest instrument is in L1
        (one row per parcel). Yamhill Clerk's per-account recordings deep
        link is not yet captured; clerk landing page is the surface until
        that's resolved.
      - Recorded sale price — not exposed by the public feed. Sale chain
        sits behind the per-account assessor portal lookup.

    Returns: {found, taxlot, county_fips, latest_instrument, owners_of_record,
              deep_links{clerk_home, assessor_home, maps_portal},
              in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _yamhill_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_yamhill", "taxlot": taxlot}

    owners = _yamhill_owner_names(row)
    has_instrument = bool(row["inst_id"] or row["inst_year"] or row["inst_type"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_yamhill L1 inline (Yamhill County Landowners last-instrument snapshot) + Yamhill County Clerk deep link",
        "latest_instrument": {
            "instrument_id": row["inst_id"] or None,
            "type": row["inst_type"] or None,
            "year": int(row["inst_year"]) if row["inst_year"] else None,
            "month": int(row["inst_month"]) if row["inst_month"] else None,
            "date_iso": _yamhill_deed_date(row),
            "sale_price": None,
        } if has_instrument else None,
        "owners_of_record": {
            "primary": owners[0] if owners else None,
            "all_owners": owners,
            "owner_count": len(owners),
            "mailing_address_1": row["mail_add1"],
            "mailing_address_2": row["mail_add2"],
            "mailing_csz": _yamhill_mailing_csz(row),
        },
        "deep_links": {
            "clerk_home": _YAMHILL_CLERK_HOMEPAGE,
            "assessor_home": _YAMHILL_ASSESSOR_HOMEPAGE,
            "maps_portal": _YAMHILL_MAPS_PORTAL,
        },
        "in_flight": [
            "Multi-instrument deed chain — L1 carries only the most-recent instrument. Yamhill Clerk's per-account recording-search deep link is not yet captured; clerk homepage is the surface until that's resolved.",
            "Recorded sale price — not exposed by the public feed. Sale chain sits behind the per-account assessor portal lookup.",
            "Instrument type (warranty / quitclaim / trustee's deed) — present for the most-recent transfer but often blank in the source's INSTTYPE column; surfacing what's there and leaving type null when missing.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_yamhill_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Yamhill (FIPS 071).

    Yamhill is Tier 2 Major. Per the emerging pattern (Tier 1 Flagships run
    county-direct Accela tenants; Tier 2/3/4 ride state Accela), Yamhill
    jurisdictions — McMinnville, Newberg, Dundee, Carlton, Lafayette, Dayton,
    Sheridan, Willamina, Yamhill (city), Amity, and unincorporated Yamhill —
    default-route through Oregon's state ePermitting (aca-oregon.accela.com /
    oregon). Yamhill FIPS '071' is in _OREGON_EPERMITTING_FIPS so this
    delegates cleanly.

    IN FLIGHT — city-portal split:
      McMinnville and Newberg are the two largest jurisdictions and MAY run
      their own city portals. Recon hit a 403 on mcminnvilleoregon.gov
      community development and a 404 on newbergoregon.gov /communitydevelopment,
      so neither city portal URL is confirmed. Until those resolve, the
      state-Accela deep link is the canonical surface for ALL Yamhill
      jurisdictions; per-city routing is queued as a follow-up.
    """
    result = fetch_county_permits(taxlot, "071", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Yamhill County (McMinnville / Newberg / Dundee / Carlton / Lafayette / "
            "Dayton / Sheridan / Willamina / Yamhill / Amity / unincorporated — "
            "Oregon state Accela; Tier 2 reuse pattern. McMinnville + Newberg city "
            "portals IN FLIGHT.)"
        )
        existing_in_flight = result.get("in_flight") or []
        result["in_flight"] = existing_in_flight + [
            "McMinnville city permit portal — recon hit 403 on mcminnvilleoregon.gov community development page; per-city deep link not yet captured. Falls back to state-Accela landing until resolved.",
            "Newberg city permit portal — recon hit 404 on newbergoregon.gov/communitydevelopment; per-city deep link not yet captured. Falls back to state-Accela landing until resolved.",
        ]
    return result
