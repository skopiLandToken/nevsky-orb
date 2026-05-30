"""
BAKER County Layer 2 + Layer 3 fetchers (TERRA — 2026-05-30, county #20).

Lives in its own module (polk_terra / columbia_terra precedent) so parallel-Yindo-
session edits on orb_db.py don't collide. Re-exported from orb_db.py via
`from .baker_terra import fetch_baker_*`.

THIN by source — the load-bearing reality (read before "enriching" this):
  Baker's only queryable taxlot source is ODOT's statewide "ames" cadastral
  (gis.odot.state.or.us .../ames/ames/MapServer/28). ODOT maintains it for road /
  right-of-way mapping, so it ships ONLY geometry + taxlot identity (MapNumber,
  Taxlot, MapTaxlot, effective year). There is NO owner, NO situs, NO valuation, NO
  property class — and for a county Baker's size that assessor data is NOT in any
  free public layer (it lives behind the Baker County Assessor Property Search, one
  parcel at a time). So these fetchers return what IS known — taxlot identity +
  acreage COMPUTED from the official polygon (ST_Area, measured not fabricated) +
  centroid — and flag owner / situs / valuation / deeds honestly IN FLIGHT per
  DOCTRINE-HONEST-IN-FLIGHT-01. Never invent an owner or value into a Baker response.

Permits: Baker is a small rural county; per the Tier-rides-state-Accela pattern it
  routes through Oregon's state ePermitting (aca-oregon.accela.com/oregon). Baker
  FIPS '001' added to _OREGON_EPERMITTING_FIPS. fetch_baker_permits wraps
  fetch_county_permits.
"""
from .orb_db import (
    _conn,
    _dt,
    _tz,
    fetch_county_permits,
)


_BAKER_HOMEPAGE = "https://www.bakercountyor.gov"
_BAKER_ASSESSOR_HOMEPAGE = "https://www.bakercountyor.gov/departments/assessor.php"
_BAKER_CLERK_HOMEPAGE = "https://www.bakercountyor.gov/departments/clerk.php"
_BAKER_ORMAP = "https://www.ormap.net"  # statewide ORMAP property-tax-map viewer (search by map/taxlot)


def _baker_l1_row(taxlot: str):
    """Fetch the L1 row for a Baker taxlot, resolving against MapTaxlot (the natural
    key, mirrored into taxlot + maptaxlot) then the short Taxlot lot suffix. Returns
    dict (incl. computed geodesic acreage + WKT centroid) or None."""
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, county_fips, maptaxlot, map_taxlot_short,
                          map_number, effective_year,
                          ROUND((ST_Area(geom::geography) / 4046.8564224)::numeric, 3) AS acres_computed,
                          ST_AsText(ST_Centroid(geom)) AS centroid,
                          ingested_at
                   FROM parcels_baker
                   WHERE taxlot = %s OR maptaxlot = %s OR map_taxlot_short = %s
                   LIMIT 1""",
                (taxlot, taxlot, taxlot),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[baker_l1_row] DB error: {e}")
        return None


_BAKER_IN_FLIGHT = [
    "Owner / mailing — NOT in the ODOT ames cadastral (it carries only geometry + taxlot identity). Baker assessor ownership lives behind the Baker County Assessor Property Search, one parcel at a time; queued if a richer Baker source is ever located.",
    "Situs address — same; not in the ODOT source.",
    "Valuation (assessed + RMV) — not in any free public Baker layer; broker pulls via the assessor by map/taxlot.",
    "Property class / building details — not in source.",
    "Recorded deed / sale chain — not in source; the Baker County Clerk is the chain-of-title hand-off.",
]


def fetch_baker_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Baker taxlot snapshot — THIN (taxlot identity + computed acreage + centroid).

    Pass-through over parcels_baker (ODOT ames cadastral). Returns the taxlot identity
    block, acreage computed from the official polygon, and the parcel centroid, plus
    deep links to the Baker County Assessor + the statewide ORMAP viewer. Owner /
    situs / valuation are honestly IN FLIGHT — not present in the ODOT source. Never
    fabricate them.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _baker_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_baker", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_baker (ODOT statewide 'ames' cadastral, layer 28) — geometry + taxlot identity only; acreage computed from polygon",
        "snapshot": {
            "identity": {
                "maptaxlot": row["maptaxlot"],
                "map_number": row["map_number"],
                "map_taxlot_short": row["map_taxlot_short"],
                "effective_year": row["effective_year"],
            },
            "acreage": {
                "computed_acres": float(row["acres_computed"]) if row["acres_computed"] is not None else None,
                "basis": "geodesic ST_Area of the ODOT cadastral polygon (no assessor-of-record acreage in source)",
            },
            "location": {
                "parcel_centroid": row["centroid"],
            },
            "owner": None,        # IN FLIGHT — not in ODOT source
            "situs": None,        # IN FLIGHT — not in ODOT source
            "valuation": None,    # IN FLIGHT — not in ODOT source
        },
        "deep_links": {
            "assessor_home": _BAKER_ASSESSOR_HOMEPAGE,
            "county_home": _BAKER_HOMEPAGE,
            "ormap_viewer": _BAKER_ORMAP,
        },
        "in_flight": _BAKER_IN_FLIGHT,
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_baker_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Baker recording hand-off for a taxlot — THIN.

    The ODOT cadastral carries no instrument/deed data, so this confirms the taxlot
    exists and routes to the Baker County Clerk for the recording chain. latest_
    instrument is null (IN FLIGHT). Never fabricate an instrument.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _baker_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_baker", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_baker (ODOT cadastral) — taxlot confirmed; recordings routed to Baker County Clerk",
        "latest_instrument": None,   # not in ODOT source — IN FLIGHT
        "owner_of_record": None,     # not in ODOT source — IN FLIGHT
        "deep_links": {
            "clerk_home": _BAKER_CLERK_HOMEPAGE,
            "assessor_home": _BAKER_ASSESSOR_HOMEPAGE,
            "ormap_viewer": _BAKER_ORMAP,
        },
        "in_flight": [
            "Recorded deed chain + owner of record — NOT in the ODOT cadastral source. The Baker County Clerk (deep link) is the chain-of-title hand-off; no per-parcel recordings API located for Baker.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_baker_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Baker (FIPS 001).

    Baker is a small rural county routing through Oregon's state ePermitting
    (aca-oregon.accela.com/oregon). Baker FIPS '001' is in _OREGON_EPERMITTING_FIPS so
    this delegates cleanly.
    """
    result = fetch_county_permits(taxlot, "001", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = (
            "Baker County (Baker City / Haines / Halfway / Huntington / Sumpter / "
            "Unity / unincorporated — Oregon state Accela)"
        )
    return result
