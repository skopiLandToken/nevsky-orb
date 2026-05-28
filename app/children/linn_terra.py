"""
LINN County Layer 2 + Layer 3 fetchers (TERRA — s5 Yindo session, 2026-05-27).

Lives in its own module (rather than inside orb_db.py with the other L2/L3
fetchers) because the s5 Linn ship landed during a five-parallel-session window
where every other session was concurrently editing orb_db.py — keeping Linn
isolated avoided merge thrash. Re-homing into orb_db.py alongside the other
counties is a clean follow-up once the parallel-session work fully lands.

Imports the shared connection helper (_conn) and primitives (_dt, _td, _tz,
_json, _httpx, _ACA_OREGON_BASE, fetch_county_permits) from orb_db so the
single source of truth stays in the same module-level globals everyone else
uses. Functions exported here are then re-imported back into orb_db.py with a
one-line `from .linn_terra import *` so child_engine.py / Sophia tool plumbing
keeps the same `from .orb_db import fetch_linn_*` contract as every other
county.

Reality of the Linn integration (carries forward to remaining mid-valley counties):

  1. parcels_linn Layer 1 is ORMAP cartographic polygons — the canonical Oregon
     ORMAP key set (Taxlot / MapTaxlot / ORTaxlot / MapNumber / ORMapNum) plus
     three acreage measures (TaxlotAcre / MapAcres / CalculatedAcres) and ORMAP
     audit provenance. It does NOT carry owner / situs / zoning / valuation /
     sale chain inline. So Linn follows the Washington-style posture: L1 is
     boundaries, L2 resolves attributes on demand.

  2. The RICH attribute surface Linn County publishes is the `pub_sales` MapServer
     (gis.co.linn.or.us/arcgis/rest/services/public/pub_sales/MapServer/0). Every
     row is a recorded sale — Recording (deed#), Instrument (WD/QC/etc), seller,
     buyer, sale_price, sale_date, prop_class, rmv_class, sqft, year_built,
     bedrooms/baths, site_addr, site_city, account, taxlot+map+PIN. PIN matches
     the parcels_linn `or_taxlot` shape (15-digit ORTaxlot e.g. "09S02E26AD00300").
     fetch_linn_assessment + fetch_linn_records both query pub_sales by PIN.

  3. Linn County's assessor public lookup portal at www.linncountyor.gov is
     Cloudflare-managed-challenge gated (probe returned the Cloudflare JS
     challenge HTML, not the page). Locating the per-account portal URL is
     IN FLIGHT — the surface exists, it's just not browser-automation-friendly.
     Until that's captured, the fetchers surface a county-assessor home deep
     link + pub_sales-derived sale snapshot (the most-recent sale row gives a
     real owner/situs/valuation pin even without the live assessor portal).

  4. Permits — Linn jurisdictions split:
       Albany     → permits.albanyoregon.gov + albanyor.buildingeye.com
                    (city-direct portal, NOT state Accela; verified during recon)
       Lebanon, Sweet Home, Brownsville, smaller cities, unincorporated Linn
                  → Oregon state ePermitting Accela
                    (Linn FIPS '043' added to _OREGON_EPERMITTING_FIPS)
     fetch_linn_permits routes by site_city from pub_sales (when known) and
     defaults to dual-surface (Albany city + state Accela deep links) when the
     jurisdiction is unknown.

  5. The brief listed Albany as "high probability for clean reuse" of
     fetch_county_permits. Recon disproves that — Albany runs its own city
     portal at permits.albanyoregon.gov. The fetch_county_permits reuse target
     remains the state Accela for the non-Albany jurisdictions only.
"""
from .orb_db import (
    _conn,
    _dt,
    _td,
    _tz,
    _json,
    _httpx,
    _ACA_OREGON_BASE,
    fetch_county_permits,
)


_LINN_CACHE_TTL_HOURS = 24
_LINN_PUB_SALES_QUERY = (
    "https://gis.co.linn.or.us/arcgis/rest/services/"
    "public/pub_sales/MapServer/0/query"
)
_LINN_ASSESSOR_HOMEPAGE = "https://www.linncountyor.gov/assessor"
_LINN_CLERK_HOMEPAGE = "https://www.linncountyor.gov/clerk"
_LINN_ALBANY_PERMITS = "https://permits.albanyoregon.gov"
_LINN_ALBANY_BUILDINGEYE = "https://albanyor.buildingeye.com/"
_LINN_STATE_ACCELA = f"{_ACA_OREGON_BASE}/Cap/CapHome.aspx?module=Building"


def _linn_l1_row(taxlot: str):
    """Resolve a Linn taxlot input against parcels_linn — accepts Taxlot suffix,
    MapTaxlot, or ORTaxlot. Returns the first matching row or None.

    `pin` is computed inline as `RPAD(map_number,10) || LPAD(taxlot,5,'0')` —
    that's the 15-char join key against Linn's pub_sales MapServer (verified via
    sample probe 2026-05-27, e.g. parcels_linn map_number='12S02W22BD' + taxlot
    '02100' → pub_sales pin '12S02W22BD02100')."""
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT id, objectid, taxlot, county_fips,
                          map_taxlot, or_taxlot, map_number, or_map_num, record_name,
                          taxlot_acre, map_acres, calculated_acres,
                          RPAD(COALESCE(map_number, ''), 10) || LPAD(taxlot, 5, '0') AS pin,
                          ST_AsText(ST_Centroid(geom)) AS centroid
                   FROM parcels_linn
                   WHERE taxlot = %s OR map_taxlot = %s OR or_taxlot = %s
                   LIMIT 1""",
                (taxlot, taxlot, taxlot),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[linn_l1_row] DB error: {e}")
        return None


def _linn_pub_sales_query(pin: str, limit: int = 25) -> list[dict]:
    """Query Linn's pub_sales MapServer by PIN (matches or_taxlot shape).

    Returns a list of sale dicts ordered most-recent-first, or [] on any error.
    """
    if not pin:
        return []
    where = f"pin='{pin}'".replace("'", "%27")
    url = (
        f"{_LINN_PUB_SALES_QUERY}?where={where}"
        f"&outFields=*&returnGeometry=false&outSR=4326&f=json"
        f"&orderByFields=saledate+DESC&resultRecordCount={limit}"
    )
    try:
        with _httpx.Client(timeout=20.0, headers={"User-Agent": "SKOpi-TERRA/1.0"}) as c:
            r = c.get(url)
            r.raise_for_status()
            data = r.json()
        sales = []
        for f in data.get("features", []) or []:
            a = f.get("attributes", {}) or {}
            saledate_ms = a.get("saledate")
            sales.append({
                "recording": a.get("Recording"),
                "instrument": a.get("Instrument"),
                "seller": a.get("seller"),
                "buyer": a.get("buyer"),
                "owner_mailing_address": " ".join(
                    filter(None, [a.get("addr1"), a.get("addr2"), a.get("addr3")])
                ).strip() or None,
                "sale_price": a.get("saleprice"),
                "sale_date_ms": saledate_ms,
                "sale_date": (
                    _dt.fromtimestamp(int(saledate_ms) / 1000, tz=_tz.utc).date().isoformat()
                    if saledate_ms else None
                ),
                "financing": a.get("financing"),
                "site_address": a.get("siteaddr"),
                "site_city": a.get("sitecity"),
                "account": a.get("account"),
                "prop_class": a.get("propcls"),
                "rmv_class": a.get("rmvcls"),
                "imp_desc": a.get("impdesc"),
                "sqft": a.get("sqft"),
                "year_built": a.get("yearblt"),
                "bedrooms": a.get("bedrooms"),
                "full_baths": a.get("fullbaths"),
                "half_baths": a.get("halfbaths"),
                "sale_acres": a.get("saleacres"),
                "grade": a.get("grade"),
                "market_area": a.get("mktarea"),
                "remark": a.get("remark"),
            })
        return sales
    except Exception as e:
        print(f"[linn_pub_sales] query error for pin={pin}: {e}")
        return []


def fetch_linn_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Linn County assessment + property snapshot for a taxlot.

    Joins parcels_linn L1 ORMAP boundary keys with the most-recent row from
    Linn's pub_sales MapServer. Cached 24h in linn_assessment_cache. Real-time
    current-roll RMV/AV + multi-year payment history stay IN FLIGHT.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, account_number, payload_json, fetched_at, last_error "
                    "FROM linn_assessment_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached and cached["payload_json"]:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_LINN_CACHE_TTL_HOURS):
                        payload = cached["payload_json"]
                        payload["from_cache"] = True
                        payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                        return payload
        except Exception as e:
            print(f"[linn_assessment] cache read error: {e}")

    row = _linn_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_linn", "taxlot": taxlot}

    sales = _linn_pub_sales_query(row["pin"], limit=25)
    latest = sales[0] if sales else None
    account = latest["account"] if latest else None

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "map_taxlot": row["map_taxlot"],
        "or_taxlot": row["or_taxlot"],
        "map_number": row["map_number"],
        "record_name": row["record_name"],
        "account_number": account,
        "county_fips": "043",
        "source": "parcels_linn L1 (ORMAP boundaries) + pub_sales MapServer (last sale snapshot)",
        "snapshot": {
            "owner_latest": latest["buyer"] if latest else None,
            "owner_mailing_address": latest["owner_mailing_address"] if latest else None,
            "situs": {
                "address": latest["site_address"] if latest else None,
                "city": latest["site_city"] if latest else None,
            },
            "last_sale": {
                "recording": latest["recording"],
                "instrument": latest["instrument"],
                "seller": latest["seller"],
                "buyer": latest["buyer"],
                "sale_price": latest["sale_price"],
                "sale_date": latest["sale_date"],
                "sale_acres": latest["sale_acres"],
            } if latest else None,
            "classification": {
                "prop_class": latest["prop_class"],
                "rmv_class": latest["rmv_class"],
                "imp_description": latest["imp_desc"],
                "grade": latest["grade"],
                "market_area": latest["market_area"],
            } if latest else None,
            "improvements": {
                "sqft": latest["sqft"],
                "year_built": latest["year_built"],
                "bedrooms": latest["bedrooms"],
                "full_baths": latest["full_baths"],
                "half_baths": latest["half_baths"],
            } if latest else None,
            "area": {
                "taxlot_acre": float(row["taxlot_acre"]) if row["taxlot_acre"] is not None else None,
                "calculated_acres": float(row["calculated_acres"]) if row["calculated_acres"] is not None else None,
                "map_acres": float(row["map_acres"]) if row["map_acres"] is not None else None,
            },
            "centroid": row["centroid"],
        },
        "deep_links": {
            "linn_assessor_home": _LINN_ASSESSOR_HOMEPAGE,
            "linn_clerk_home": _LINN_CLERK_HOMEPAGE,
        },
        "in_flight": [
            "Current-year RMV / AV / certified-tax + multi-year payment history — Linn assessor portal is Cloudflare managed-challenge gated; portal URL contract not captured this pass. Snapshot above is the LAST-SALE row from pub_sales, not the current roll.",
            f"Sale history depth — pub_sales returned {len(sales)} row(s) for this PIN. Use fetch_linn_records for the full chain.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO linn_assessment_cache (taxlot, account_number, payload_json, fetched_at, last_error)
                   VALUES (%s, %s, %s::jsonb, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       account_number = EXCLUDED.account_number,
                       payload_json = EXCLUDED.payload_json,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, account, _json.dumps(payload)),
            )
            cn.commit()
    except Exception as e:
        print(f"[linn_assessment] cache write error: {e}")

    return payload


def fetch_linn_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Linn recorded-documents / deed chain / sales history for a taxlot.

    Every pub_sales row IS a deed Recording. Returns the full chain by PIN
    most-recent-first. Cached 24h in linn_records_cache.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, owner_name, documents_json, document_count, fetched_at "
                    "FROM linn_records_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_LINN_CACHE_TTL_HOURS):
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "owner_name": cached["owner_name"],
                            "documents": cached["documents_json"] or [],
                            "document_count": cached["document_count"] or 0,
                            "deep_links": {
                                "linn_clerk_home": _LINN_CLERK_HOMEPAGE,
                                "linn_assessor_home": _LINN_ASSESSOR_HOMEPAGE,
                            },
                            "in_flight": [
                                "Linn County Clerk recordings direct search portal — county site Cloudflare-gated; portal URL contract not yet captured.",
                                "Older deeds (pre-pub_sales coverage) not available via this surface.",
                            ],
                            "fetched_at": cached["fetched_at"].isoformat(),
                        }
        except Exception as e:
            print(f"[linn_records] cache read error: {e}")

    row = _linn_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_linn", "taxlot": taxlot}

    sales = _linn_pub_sales_query(row["pin"], limit=100)
    latest_owner = sales[0]["buyer"] if sales else None

    documents = [
        {
            "recording": s["recording"],
            "instrument": s["instrument"],
            "grantor": s["seller"],
            "grantee": s["buyer"],
            "sale_date": s["sale_date"],
            "sale_price": s["sale_price"],
            "sale_acres": s["sale_acres"],
            "financing": s["financing"],
            "site_address": s["site_address"],
            "site_city": s["site_city"],
            "remark": s["remark"],
        }
        for s in sales
    ]

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "map_taxlot": row["map_taxlot"],
        "or_taxlot": row["or_taxlot"],
        "county_fips": "043",
        "owner_name": latest_owner,
        "snapshot": {
            "owner_now": latest_owner,
            "first_recording": sales[-1]["sale_date"] if sales else None,
            "last_recording": sales[0]["sale_date"] if sales else None,
        },
        "documents": documents,
        "document_count": len(documents),
        "deep_links": {
            "linn_clerk_home": _LINN_CLERK_HOMEPAGE,
            "linn_assessor_home": _LINN_ASSESSOR_HOMEPAGE,
        },
        "in_flight": [
            "Linn County Clerk recordings direct search portal — county site Cloudflare-gated; portal URL contract not yet captured. pub_sales is the primary deed surface for now.",
            "Older deeds (pre-pub_sales coverage) not available via this surface.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO linn_records_cache (taxlot, owner_name, documents_json, document_count, fetched_at, last_error)
                   VALUES (%s, %s, %s::jsonb, %s, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       owner_name = EXCLUDED.owner_name,
                       documents_json = EXCLUDED.documents_json,
                       document_count = EXCLUDED.document_count,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, latest_owner, _json.dumps(documents), len(documents)),
            )
            cn.commit()
    except Exception as e:
        print(f"[linn_records] cache write error: {e}")

    return payload


def fetch_linn_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Linn permits — branches by site_city (pulled from latest pub_sales row).

    Albany jurisdiction → permits.albanyoregon.gov + albanyor.buildingeye.com.
    Other jurisdictions → Oregon state Accela via fetch_county_permits('043').
    Unknown jurisdiction → dual-surface deep links.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _linn_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_linn", "taxlot": taxlot}

    sales = _linn_pub_sales_query(row["pin"], limit=1)
    # pub_sales `sitecity` is a CSZ blob ("ALBANY OR 97321"), not just the city.
    # Extract first whitespace-delimited token for jurisdiction routing.
    site_city_raw = (sales[0].get("site_city") or "").strip().upper() if sales else ""
    site_city = site_city_raw.split()[0] if site_city_raw else ""
    site_address = sales[0].get("site_address") if sales else None

    if site_city == "ALBANY":
        return {
            "found": True,
            "from_cache": False,
            "taxlot": taxlot,
            "or_taxlot": row["or_taxlot"],
            "county_fips": "043",
            "routed_via": "albany_city",
            "jurisdiction": "City of Albany (city-direct portal — NOT state Accela)",
            "site_address": site_address,
            "site_city": site_city,
            "status": "deep_link_only",
            "permit_count": 0,
            "permits": [],
            "deep_links": {
                "albany_permits": _LINN_ALBANY_PERMITS,
                "albany_buildingeye": _LINN_ALBANY_BUILDINGEYE,
            },
            "in_flight": [
                "Structured scrape of Albany's city e-Permitting portal — endpoint contract not captured this pass. Albany runs its own city system (not state Accela — distinct stack).",
                "Until then: clicking permits.albanyoregon.gov lands the user on Albany e-Permitting; albanyor.buildingeye.com is the Agency Counter complement.",
            ],
            "fetched_at": _dt.now(_tz.utc).isoformat(),
        }

    if not site_city:
        return {
            "found": True,
            "from_cache": False,
            "taxlot": taxlot,
            "or_taxlot": row["or_taxlot"],
            "county_fips": "043",
            "routed_via": "dual_surface_unknown_jurisdiction",
            "jurisdiction": "Jurisdiction unknown — taxlot has no recorded sale with site_city (likely undeveloped / no recent transaction)",
            "site_address": site_address,
            "site_city": None,
            "status": "deep_link_only",
            "permit_count": 0,
            "permits": [],
            "deep_links": {
                "albany_permits": _LINN_ALBANY_PERMITS,
                "albany_buildingeye": _LINN_ALBANY_BUILDINGEYE,
                "oregon_state_accela": _LINN_STATE_ACCELA,
            },
            "in_flight": [
                "Jurisdiction resolution from parcels_linn — would need a CityLimits spatial join (planned alongside the unified `parcels` migration). Until then dual-surface deep links are returned.",
                "Same deep-link-only scrape blocker on the underlying portals (viewstate-gated).",
            ],
            "fetched_at": _dt.now(_tz.utc).isoformat(),
        }

    # Lebanon / Sweet Home / Brownsville / Mill City / Lyons / Scio / Harrisburg /
    # Halsey / Tangent / Crabtree / Shedd / Idanha / unincorporated Linn → state Accela.
    result = fetch_county_permits(taxlot, "043", force_refresh)
    if isinstance(result, dict):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = f"{site_city.title()} / unincorporated Linn (Oregon state Accela)"
        result["site_address"] = site_address
        result["site_city"] = site_city
        if "deep_links" in result and isinstance(result["deep_links"], dict):
            result["deep_links"].setdefault("albany_permits_alt", _LINN_ALBANY_PERMITS)
    return result
