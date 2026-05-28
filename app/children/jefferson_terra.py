"""
JEFFERSON County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-28, Tier 3, completes
the Central Oregon trio: Deschutes (Tier 1) + Jefferson + Crook (Tier 3)).

Lives in its own module (Clatsop / Tillamook / Lincoln / Polk / Yamhill precedent)
rather than appended to orb_db.py — the YINDO_BRIEF_TERRA_JEFFERSON decision rule:
land in orb_db.py end-of-file UNLESS a sibling Tier 3 is mid-build on the shared
files, in which case split out a {county}_terra.py to dodge the concurrent-edit
race. A sibling WAS mid-build on child_engine.py (Lincoln) at ship time, so this
module is the safe call. Re-exported from orb_db.py via
`from .jefferson_terra import fetch_jefferson_*` so the public contract matches
every other county.

Reality of the Jefferson integration — READ before "fixing" the source:

  1. parcels_jefferson L1 comes from the Deschutes-hosted COFSA fire-mapping
     aggregator (maps.deschutes.org/.../COFSA_Map/MapServer/3, "Jefferson County
     Taxlots", capabilities Map,Query,Data, 12,813 lots) — NOT the ODF statewide
     TaxlotsDisplay overlay. The brief originally speced ODF Layer 15 (it advertises
     the full 54-field Yamhill-family schema), but ODF's service capability is "Map"
     only: every /query returns 400 "operation not supported". Display-reachable is
     not queryable. COFSA is the real queryable Jefferson source. See the ingest
     script docstring + commit 45cb0c4 for the full source-decision reasoning.

  2. COFSA is a THINner cadastre than the rich migration target. It carries OWNER
     (single line), situs components (HOUSE_NUMB..STREET..CITY..ZIP), MAPTAXLOT,
     and geometry — and NOTHING else. So fetch_jefferson_assessment is a SQL
     pass-through over parcels_jefferson L1 inline (Clatsop pattern) — no scraper,
     no viewstate, no per-call HTTP.

  3. What L1 does NOT carry (flagged IN FLIGHT honestly, NEVER invented):
       Acreage    — COFSA ships ACRES 0% populated. taxlot_acres is NULL across all
                    12,813 rows. We derive geodesic acres from geom at query time
                    (ST_Area(geom::geography) / 4046.8564224) so reports never render
                    empty acres. Enrichment-from-geometry, surfaced as such.
       Valuation  — no assessed / RMV fields in COFSA. Broker hits the assessor
                    portal (jeffersoncountyor.gov/assessor — Cloudflare-blocked from
                    the droplet, fine in a browser).
       Recorded
       instrument — no INSTID/INSTYEAR family in COFSA. Chain of title behind the
                    Clerk surface.
       PLSS / mailing / owner_line2-3 / prp_class — columns exist in the table
                    (Yamhill-family, KB_87 forward-compat) but COFSA leaves them
                    NULL. They surface automatically if a richer source backfills.
       Zoning     — no Jefferson zoning service located yet (Jackson pattern ingest
                    queued). Null at first ship.

  4. Permits — Jefferson is Tier 3. FIPS '031' is already in _OREGON_EPERMITTING_FIPS
     (added during the Crook ship), so fetch_jefferson_permits is a one-line wrapper
     over fetch_county_permits — Madras / Culver / Metolius + unincorporated all
     ride Oregon state ePermitting (aca-oregon.accela.com/oregon).

  Warm Springs Reservation: sovereign tribal land is generally excluded from county
  taxlot publication; expect not-in-coverage west of the Deschutes River. (COFSA
  does carry a few Confederated-Tribes-owned parcels, so coverage is partial — a
  pin-drop deep in the reservation returning not-in-coverage is correct, not a bug.)
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


# Jefferson County deep-link targets. Per the brief (Risk 2/3): the assessor and
# clerk portals are Cloudflare-blocked from the droplet and the vendor is TBD, so
# these are homepage-level hand-offs, NOT constructed per-account deep links.
_JEFFERSON_HOME = "https://www.jeffersoncountyor.gov"
_JEFFERSON_ASSESSOR = "https://www.jeffersoncountyor.gov/assessor"
_JEFFERSON_CLERK = "https://www.jeffersoncountyor.gov/clerk"
_JEFFERSON_COFSA_REST_BASE = (
    "https://maps.deschutes.org/arcgis/rest/services/911/COFSA_Map/MapServer/3"
)


def _jefferson_l1_row(taxlot: str):
    """Fetch the L1 row for a Jefferson taxlot. COFSA's only natural key is
    MAPTAXLOT (stored in both taxlot and maptaxlot), so resolve on either, plus
    prim_acc_num for forward-compat (NULL today, populated if a richer source
    backfills). Acreage is computed geodesically from geom — COFSA carries no
    acreage column (ACRES is 0% populated)."""
    if not taxlot:
        return None
    canon = taxlot.strip().upper()
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT id, objectid, taxlot, maptaxlot, county_fips,
                          map_number, ormapnum, prim_acc_num, si_map_tax,
                          owner_line1, owner_line2, owner_line3, agent_name,
                          mail_add1, mail_add2, mail_city, mail_state, mail_zip, mail_country,
                          site_add_nam, site_add_cty, site_zip,
                          inst_id, inst_type, inst_year, inst_month,
                          prp_class, prp_cls_desc, taxlot_acres,
                          ROUND((ST_Area(geom::geography) / 4046.8564224)::numeric, 3) AS geodesic_acres,
                          ingested_at, last_modified
                   FROM parcels_jefferson
                   WHERE taxlot = %s OR maptaxlot = %s OR prim_acc_num = %s
                   LIMIT 1""",
                (canon, canon, canon),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[jefferson_l1_row] DB error: {e}")
        return None


def _jefferson_owner_names(row) -> list[str]:
    out = []
    for k in ("owner_line1", "owner_line2", "owner_line3"):
        v = (row[k] or "").strip()
        if v:
            out.append(v)
    return out


def _jefferson_situs(row) -> str | None:
    s = (row["site_add_nam"] or "").strip()
    if not s:
        return None
    city = (row["site_add_cty"] or "").strip()
    return f"{s}, {city}" if city else s


def _jefferson_acres(row):
    """Native acreage if a backfill ever lands it; otherwise the geodesic derive."""
    if row["taxlot_acres"] is not None:
        return float(row["taxlot_acres"]), "native (taxlot_acres)"
    if row["geodesic_acres"] is not None:
        return float(row["geodesic_acres"]), "geometry-derived (COFSA carries no native acreage)"
    return None, None


def fetch_jefferson_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Jefferson assessment + ownership + situs snapshot for a taxlot.

    Pass-through over parcels_jefferson L1 inline (no external HTTP). COFSA is a
    thin cadastre: ships OWNER (single line) + situs + MAPTAXLOT + geometry. Acreage
    is GEOMETRY-DERIVED (geodesic) because COFSA ships no native acreage. Owner
    stack / mailing / valuation / instrument / PLSS / prp_class are IN FLIGHT —
    the columns exist (Yamhill-family, KB_87 forward-compat) but COFSA leaves them
    NULL; they surface automatically if a richer source backfills. Honest framing
    per DOCTRINE-HONEST-IN-FLIGHT-01.

    Returns: {found, taxlot, county_fips, snapshot{owner, situs, account,
              classification, acreage, valuation}, deep_links, in_flight[],
              fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _jefferson_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_jefferson", "taxlot": canon}

    owners = _jefferson_owner_names(row)
    acres, acres_source = _jefferson_acres(row)

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_jefferson L1 inline (Deschutes COFSA Jefferson Taxlots aggregator) + Jefferson County deep links",
        "snapshot": {
            "owner": {
                "primary": owners[0] if owners else None,
                "all_owners": owners,
                "owner_count": len(owners),
                "mailing": {
                    "street": (row["mail_add1"] or "").strip() or None,
                    "street_2": (row["mail_add2"] or "").strip() or None,
                    "city": (row["mail_city"] or "").strip() or None,
                    "state": (row["mail_state"] or "").strip() or None,
                    "zip": (row["mail_zip"] or "").strip() or None,
                },
            },
            "situs": {
                "address": _jefferson_situs(row),
                "raw_situs": row["site_add_nam"],
                "city": row["site_add_cty"],
                "zip": row["site_zip"],
            },
            "account": {
                "maptaxlot": row["maptaxlot"],
                "prim_acc_num": row["prim_acc_num"],
                "map_number": row["map_number"],
                "ormapnum": row["ormapnum"],
                "si_map_tax": row["si_map_tax"],
            },
            "classification": {
                "prp_class": row["prp_class"],
                "prp_cls_desc": row["prp_cls_desc"],
            },
            "acreage": {
                "acres": acres,
                "acres_source": acres_source,
            },
            "valuation": {
                "assessed_total": None,
                "rmv_total": None,
                "land_value": None,
                "improvement_value": None,
            },
        },
        "deep_links": {
            "assessor": _JEFFERSON_ASSESSOR,
            "clerk_records": _JEFFERSON_CLERK,
            "county_home": _JEFFERSON_HOME,
            "taxlot_rest": (
                f"{_JEFFERSON_COFSA_REST_BASE}/query?"
                f"where=MAPTAXLOT%3D%27{row['maptaxlot']}%27&outFields=*&f=json"
            ),
        },
        "in_flight": [
            "Acreage is GEOMETRY-DERIVED (geodesic from the parcel polygon) — COFSA ships ACRES 0% populated, so there is no native acreage. The figure is accurate to the polygon but is not the assessor's stated acreage; confirm against the assessor portal for legal acreage.",
            "Valuation (assessed / RMV, land / improvement split) — COFSA carries NO valuation fields. Broker pulls live values from the Jefferson County Assessor (jeffersoncountyor.gov/assessor). Vendor TBD (Cloudflare-blocked from server; fine in a browser).",
            "Owner stack beyond the primary line, mailing address, recorded instrument / sale price, PLSS section, and property class — the columns exist in the table (Yamhill-family forward-compat) but the COFSA source leaves them NULL. They surface automatically if a richer county-direct or DOR source is wired.",
            "Zoning — no Jefferson zoning service located yet; a jefferson_zoning ingest + spatial join is queued (Jackson pattern). Null at first ship.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_jefferson_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Jefferson recorded-document snapshot for a taxlot.

    Returns the current owner of record from parcels_jefferson L1 inline plus deep
    links to the Jefferson County Clerk and Assessor surfaces. COFSA carries NO
    recorded-instrument data, so the recording chain is entirely IN FLIGHT —
    surfaced via deep link until a structured recording source is wired. Honest
    framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    canon = taxlot.strip().upper()

    row = _jefferson_l1_row(canon)
    if not row:
        return {"found": False, "reason": f"Taxlot {canon} not in parcels_jefferson", "taxlot": canon}

    owners = _jefferson_owner_names(row)

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_jefferson L1 inline owner-of-record (COFSA) + Jefferson County recording deep links",
        "latest_recorded_sale": None,
        "latest_instrument": None,
        "owner_of_record": {
            "primary": owners[0] if owners else None,
            "all_owners": owners,
            "owner_count": len(owners),
        },
        "deep_links": {
            "clerk_records": _JEFFERSON_CLERK,
            "assessor": _JEFFERSON_ASSESSOR,
            "county_home": _JEFFERSON_HOME,
        },
        "in_flight": [
            "Full recorded-instrument chain of title (deed history, instrument numbers, recording dates) — NOT exposed by the COFSA Jefferson Taxlots layer; no instrument fields in source. The Jefferson County Clerk surface is the broker hand-off until a structured recording source is wired.",
            "Recorded sale price — not in source. Behind the Clerk / Assessor surface.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_jefferson_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Jefferson (FIPS 031).

    Jefferson is Tier 3. FIPS '031' was added to _OREGON_EPERMITTING_FIPS during
    the Crook ship, so all Jefferson jurisdictions — Madras, Culver, Metolius, and
    unincorporated Jefferson — default-route through Oregon's state ePermitting
    (aca-oregon.accela.com/oregon). Honest IN FLIGHT framing per
    DOCTRINE-HONEST-IN-FLIGHT-01: deep-link-only until the viewstate / Playwright
    unlock lands, same as every other state-Accela county.
    """
    result = fetch_county_permits(taxlot, "031", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Jefferson County (Madras / Culver / Metolius / unincorporated — "
            "Oregon state Accela; Tier 3 reuse pattern)"
        )
    return result
