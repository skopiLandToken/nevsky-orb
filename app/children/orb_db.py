"""
ORB DB — shared read-only database access for ORB children (Sophia, Ophelia, etc.)

Doctrine: This module is read-only by design. Children NEVER write through it.
Writes go through the api layer (main.py) where audit logging and authorization
checks happen. Read-and-recommend is the child pattern; write-with-confirmation
is the human-in-the-loop pattern.

All functions here are SAFE to expose to LLM tool-use because they cannot mutate
state. The only risk surface is information disclosure — handled via the
calling child's authority level (Sophia: full; Ophelia: filtered; Hypatia: none).
"""
import os
import logging
from datetime import datetime, timezone
import psycopg
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")


def _conn():
    """Open a new connection. Caller is responsible for context-managing it."""
    return psycopg.connect(DB_DSN, row_factory=dict_row)


# ============================================================================
# KNOWLEDGE STORE — the canonical KB
# ============================================================================

def search_knowledge_store(
    query: str | None = None,
    content_type: str | None = None,
    tags: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Search knowledge_store by free-text + optional content_type + optional tag overlap.
    
    Use cases:
        - "what did Yakov and I do today?" -> tags=["2026-04-27"]
        - "show me adversary doctrine" -> tags=["adversary", "doctrine"]
        - "find the Reindeer agreement" -> query="Reindeer", content_type="agreement"
    
    Returns list of dicts with id, title, content_type, tags, created_at, content (truncated).
    """
    where_clauses = ["TRUE"]
    params: list = []
    
    if query:
        where_clauses.append("(title ILIKE %s OR content ILIKE %s)")
        like = f"%{query}%"
        params.extend([like, like])
    
    if content_type:
        where_clauses.append("content_type = %s")
        params.append(content_type)
    
    if tags:
        where_clauses.append("tags && %s")
        params.append(tags)
    
    sql = f"""
        SELECT id, title, content_type, tags, created_at, is_private,
               LEFT(content, 2000) AS content_preview,
               LENGTH(content) AS content_full_length
        FROM knowledge_store
        WHERE {" AND ".join(where_clauses)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(limit)
    
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("search_knowledge_store error: %s", e)
        return []


def get_knowledge_entry_full(entry_id: str) -> dict | None:
    """
    Retrieve the FULL content of a single knowledge_store entry by id.
    Use after search_knowledge_store identifies a relevant entry and you need
    the complete text rather than the 2000-char preview.
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT id, title, content, content_type, tags, source, source_type, created_at, is_private FROM knowledge_store WHERE id = %s",
                (entry_id,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("get_knowledge_entry_full error: %s", e)
        return None


# ============================================================================
# USERS — non-tombstoned by default
# ============================================================================

def get_active_users() -> list[dict]:
    """Return all non-tombstoned users with key fields for org awareness."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, full_name, email, role, telegram_user_id, is_active, created_at
                FROM users
                WHERE tombstoned_at IS NULL
                ORDER BY created_at ASC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_active_users error: %s", e)
        return []


def get_tombstoned_users() -> list[dict]:
    """Return all tombstoned users — for adversary/historical awareness."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, full_name, email, role, telegram_user_id,
                       tombstoned_at, tombstone_reason
                FROM users
                WHERE tombstoned_at IS NOT NULL
                ORDER BY tombstoned_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_tombstoned_users error: %s", e)
        return []


# ============================================================================
# BLOCKLIST — adversary awareness
# ============================================================================

def get_blocklist() -> list[dict]:
    """Return all blocked actors with full reasoning."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, telegram_user_id, user_uuid, block_reason, block_severity,
                       blocked_at, notes
                FROM personas_blocklist
                ORDER BY blocked_at DESC
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_blocklist error: %s", e)
        return []


def get_honeypot_hits(limit: int = 20) -> list[dict]:
    """Return recent contact attempts caught by the honeypot guard."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, received_at, bot_name, telegram_user_id,
                       message_text, alert_sent, alert_sent_at
                FROM fred_contact_log
                ORDER BY received_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_honeypot_hits error: %s", e)
        return []


# ============================================================================
# OPERATIONAL — tasks, site plans, capital
# ============================================================================

def get_recent_yakov_tasks(limit: int = 10) -> list[dict]:
    """Recent automation tasks from yakov_tasks queue."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, task_type, status, created_at, executed_at,
                       error_message
                FROM yakov_tasks
                ORDER BY created_at DESC
                LIMIT %s
            """, (limit,))
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_recent_yakov_tasks error: %s", e)
        return []


def get_site_plans() -> list[dict]:
    """Site plan analyses — Reindeer and any future plats."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT id, filename, plan_title, total_lots,
                       price_per_lot, total_gross_value, created_at
                FROM site_plans
                ORDER BY created_at DESC
                LIMIT 20
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error("get_site_plans error: %s", e)
        return []


# ============================================================================
# AI USAGE — cost monitoring
# ============================================================================

def get_ai_spend_today() -> dict:
    """
    Return today's AI spend breakdown.
    
    Lesson for Nevsky: cost observability is part of operational maturity.
    This function feeds the Sophia spend-check command Iosif runs ad-hoc.
    """
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT 
                    COALESCE(SUM(estimated_cost_usd), 0) AS total_today,
                    COUNT(*) AS call_count,
                    COALESCE(SUM(input_tokens), 0) AS input_tokens_total,
                    COALESCE(SUM(output_tokens), 0) AS output_tokens_total
                FROM ai_usage_logs
                WHERE created_at >= CURRENT_DATE AT TIME ZONE 'America/Los_Angeles'
            """)
            today = dict(cur.fetchone() or {})
            
            cur.execute("""
                SELECT model, COUNT(*) AS calls,
                       COALESCE(SUM(estimated_cost_usd), 0) AS cost
                FROM ai_usage_logs
                WHERE created_at >= CURRENT_DATE AT TIME ZONE 'America/Los_Angeles'
                GROUP BY model
                ORDER BY cost DESC
            """)
            by_model = [dict(r) for r in cur.fetchall()]
            
            return {"today": today, "by_model": by_model}
    except Exception as e:
        logger.error("get_ai_spend_today error: %s", e)
        return {"today": {}, "by_model": [], "error": str(e)}



# =============================================================================
# TERRA County Registry — multi-county spatial lookup
# =============================================================================
# Adding a new county = one entry below. The query string returns a normalized
# column set; the function loops registered counties whose bbox contains the
# point and returns the first hit. Counties don't overlap, so first hit wins.
#
# Normalized output columns each query must produce (use NULL::text for fields
# the county doesn't have natively):
#   taxlot, section_id, acres, county_url, map_number, owner_name,
#   situs_address, zone_code, zone_label, parcel_centroid, county_name, county_fips
# =============================================================================

COUNTY_REGISTRY = {
    "017": {  # Deschutes (FIPS 41-017)
        "name": "Deschutes",
        # bbox: (lat_min, lat_max, lon_min, lon_max)
        "bbox": (43.5, 44.5, -122.1, -120.5),
        "query": """
            SELECT taxlot,
                   township || '-' || range_ || '-' || section AS section_id,
                   ROUND((shape_area / 43560.0)::numeric, 3) AS acres,
                   dial_url AS county_url,
                   mapnumber AS map_number,
                   NULL::text AS owner_name,
                   NULL::text AS situs_address,
                   zone_code,
                   zone_jurisdiction AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Deschutes'::text AS county_name,
                   '017'::text AS county_fips
            FROM parcels_deschutes
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "013": {  # Crook (FIPS 41-013)
        "name": "Crook",
        "bbox": (44.05, 44.50, -121.10, -119.80),
        "query": """
            SELECT taxlot,
                   map_number AS section_id,
                   ROUND(COALESCE(gis_acres, assessed_acres)::numeric, 3) AS acres,
                   pats_url AS county_url,
                   map_number,
                   owner_name,
                   situs_address,
                   zone_code,
                   zone_desc AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Crook'::text AS county_name,
                   '013'::text AS county_fips
            FROM parcels_crook
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    # Jefferson (031) and Lane (039) entries land here when their L1 tables exist.
}


def lookup_parcel_by_point(latitude: float, longitude: float) -> dict:
    """Spatial lookup: county parcel containing a lat/lon point.

    Iterates COUNTY_REGISTRY; counties don't overlap so first-hit wins. Returns
    a normalized shape that's backward-compatible with the original Deschutes-only
    contract (taxlot, section_id, acres, dial_url, mapnumber, parcel_centroid,
    development_status, development_note, queried_lat/lon all preserved) plus new
    multi-county fields (county, county_fips, county_url, owner_name, situs_address,
    zone_code, zone_label, map_number).
    """
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return {"found": False, "reason": "Invalid coordinates"}

    candidates = [
        (fips, entry) for fips, entry in COUNTY_REGISTRY.items()
        if entry["bbox"][0] <= lat <= entry["bbox"][1]
        and entry["bbox"][2] <= lon <= entry["bbox"][3]
    ]
    if not candidates:
        covered = ", ".join(e["name"] for e in COUNTY_REGISTRY.values())
        return {
            "found": False,
            "reason": f"Point ({lat}, {lon}) outside TERRA-covered counties. Currently covering: {covered}.",
        }

    with _conn() as conn:
        with conn.cursor() as cur:
            for _fips, entry in candidates:
                cur.execute(entry["query"], (lon, lat))
                row = cur.fetchone()
                if row:
                    county_url = row["county_url"]
                    county_fips = row["county_fips"]
                    return {
                        "found": True,
                        "taxlot": row["taxlot"],
                        "county": row["county_name"],
                        "county_fips": county_fips,
                        "section_id": row["section_id"],
                        "acres": float(row["acres"]) if row["acres"] is not None else None,
                        "county_url": county_url,
                        # Legacy alias — Deschutes-era callers read dial_url. Only populated
                        # for Deschutes so a Crook lookup can't accidentally hand a non-DIAL
                        # URL to a fetch_dial_* tool.
                        "dial_url": county_url if county_fips == "017" else None,
                        "map_number": row["map_number"],
                        "mapnumber": row["map_number"],  # legacy key
                        "owner_name": row["owner_name"],
                        "situs_address": row["situs_address"],
                        "zone_code": row["zone_code"],
                        "zone_label": row["zone_label"],
                        "parcel_centroid": row["parcel_centroid"],
                        "development_status": "unknown",
                        "development_note": "Tier 2 permit watch not yet built. Permit-derived signals coming.",
                        "queried_lat": lat,
                        "queried_lon": lon,
                    }

    return {
        "found": False,
        "reason": f"No parcel contains point ({lat}, {lon}). May be road or right-of-way.",
    }



# =============================================================================
# DIAL Permit Fetcher (TERRA-FIELD-7A)
# =============================================================================
import re as _re
import json as _json
import httpx as _httpx
from bs4 import BeautifulSoup as _BS
from datetime import datetime as _dt, timedelta as _td, timezone as _tz

_DIAL_BASE = "http://dial.deschutes.org"
_DIAL_USER_AGENT = "Mozilla/5.0 SKOpi-TERRA/1.0 (+https://skopi.io)"
_DIAL_CACHE_TTL_HOURS = 24


def _dial_resolve_property_id(taxlot: str) -> str | None:
    """Hit taxlot endpoint, follow 302 to find /Real/Index/{property_id}."""
    url = f"{_DIAL_BASE}/results/taxlot?value={taxlot}"
    try:
        with _httpx.Client(follow_redirects=False, timeout=15.0,
                           headers={"User-Agent": _DIAL_USER_AGENT}) as c:
            r = c.get(url)
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                m = _re.search(r"/Real/Index/(\d+)", loc)
                if m:
                    return m.group(1)
            # Fallback: maybe page rendered directly with a Permits link
            soup = _BS(r.text, "html.parser")
            for a in soup.find_all("a", href=True):
                m = _re.search(r"/Real/Permits/(\d+)", a["href"])
                if m:
                    return m.group(1)
    except Exception as e:
        print(f"[dial] resolve property_id error: {e}")
    return None


def _dial_parse_permits_html(html: str, property_id: str) -> list[dict]:
    """Parse the infoTable on /Real/Permits/{property_id} into a list of permit dicts."""
    soup = _BS(html, "html.parser")
    table = soup.find("table", class_="infoTable")
    if not table:
        return []

    permits = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 5:
            continue
        # First cell typically contains an <a> with the permit ID + deeplink
        first_link = cells[0].find("a", href=True)
        permit_id = (first_link.get_text(strip=True) if first_link else cells[0].get_text(strip=True))
        deeplink = (_DIAL_BASE + first_link["href"]) if first_link else None

        permits.append({
            "permit_id": permit_id,
            "permit_type": cells[1].get_text(strip=True),
            "permit_name": cells[2].get_text(strip=True),
            "application_date": cells[3].get_text(strip=True),
            "status": cells[4].get_text(strip=True),
            "deeplink": deeplink,
        })

    return permits


def fetch_dial_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """
    Fetch permit list for a Deschutes parcel from the county DIAL system.
    Returns {found: bool, taxlot, property_id, permit_count, permits, fetched_at, cached_age_hours, dial_permits_url}.
    Cached 24h in dial_permit_cache table — idempotent, polite to county servers.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    # Cache check
    if not force_refresh:
        try:
            with _conn() as cn:
                with cn.cursor() as cur:
                    cur.execute(
                        "SELECT taxlot, property_id, permits_json, permit_count, fetched_at, last_error FROM dial_permit_cache WHERE taxlot = %s",
                        (taxlot,),
                    )
                    row = cur.fetchone()
                    if row:
                        age = _dt.now(_tz.utc) - row["fetched_at"]
                        if age < _td(hours=_DIAL_CACHE_TTL_HOURS):
                            return {
                                "found": True,
                                "from_cache": True,
                                "cached_age_hours": round(age.total_seconds() / 3600, 1),
                                "taxlot": taxlot,
                                "property_id": row["property_id"],
                                "permit_count": row["permit_count"],
                                "permits": row["permits_json"] or [],
                                "fetched_at": row["fetched_at"].isoformat(),
                                "dial_permits_url": f"{_DIAL_BASE}/Real/Permits/{row['property_id']}" if row["property_id"] else None,
                            }
        except Exception as e:
            print(f"[dial] cache read error: {e}")

    # Live fetch
    property_id = _dial_resolve_property_id(taxlot)
    if not property_id:
        err = f"Could not resolve property_id for taxlot {taxlot}. May not exist in DIAL."
        try:
            with _conn() as cn:
                with cn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO dial_permit_cache (taxlot, property_id, permits_json, permit_count, fetched_at, last_error)
                           VALUES (%s, NULL, '[]'::jsonb, 0, NOW(), %s)
                           ON CONFLICT (taxlot) DO UPDATE SET fetched_at = NOW(), last_error = EXCLUDED.last_error""",
                        (taxlot, err),
                    )
                    cn.commit()
        except Exception:
            pass
        return {"found": False, "reason": err}

    permits_url = f"{_DIAL_BASE}/Real/Permits/{property_id}"
    try:
        with _httpx.Client(follow_redirects=True, timeout=20.0,
                           headers={"User-Agent": _DIAL_USER_AGENT}) as c:
            r = c.get(permits_url)
            r.raise_for_status()
            permits = _dial_parse_permits_html(r.text, property_id)
    except Exception as e:
        return {"found": False, "reason": f"DIAL fetch error: {e}", "property_id": property_id, "dial_permits_url": permits_url}

    # Save to cache
    try:
        with _conn() as cn:
            with cn.cursor() as cur:
                cur.execute(
                    """INSERT INTO dial_permit_cache (taxlot, property_id, permits_json, permit_count, fetched_at, last_error)
                       VALUES (%s, %s, %s::jsonb, %s, NOW(), NULL)
                       ON CONFLICT (taxlot) DO UPDATE SET
                           property_id = EXCLUDED.property_id,
                           permits_json = EXCLUDED.permits_json,
                           permit_count = EXCLUDED.permit_count,
                           fetched_at = NOW(),
                           last_error = NULL""",
                    (taxlot, property_id, _json.dumps(permits), len(permits)),
                )
                cn.commit()
    except Exception as e:
        print(f"[dial] cache write error: {e}")

    return {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "property_id": property_id,
        "permit_count": len(permits),
        "permits": permits,
        "dial_permits_url": permits_url,
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }



# =============================================================================
# DIAL Section Scraper (TERRA-FIELD-7B)
# Generic infoTable parser — wrappers for Sales, Valuation, DevDocs, etc.
# =============================================================================
_DIAL_SECTION_TTL_HOURS = 24


def _dial_fetch_section(taxlot: str, section: str, force_refresh: bool = False) -> dict:
    """
    Generic fetcher for any /Real/{section}/{property_id} DIAL endpoint that
    renders a <table class='infoTable'>. Returns parsed rows + metadata.
    Cached per (taxlot, section) for 24h in dial_section_cache.
    """
    if not taxlot or not section:
        return {"found": False, "reason": "Missing taxlot or section"}
    taxlot = taxlot.strip().upper()
    section = section.strip()

    # Cache check
    if not force_refresh:
        try:
            with _conn() as cn:
                with cn.cursor() as cur:
                    cur.execute(
                        "SELECT taxlot, section, property_id, rows_json, row_count, fetched_at FROM dial_section_cache WHERE taxlot = %s AND section = %s",
                        (taxlot, section),
                    )
                    row = cur.fetchone()
                    if row:
                        age = _dt.now(_tz.utc) - row["fetched_at"]
                        if age < _td(hours=_DIAL_SECTION_TTL_HOURS):
                            return {
                                "found": True,
                                "from_cache": True,
                                "cached_age_hours": round(age.total_seconds() / 3600, 1),
                                "taxlot": taxlot,
                                "section": section,
                                "property_id": row["property_id"],
                                "row_count": row["row_count"],
                                "rows": row["rows_json"] or [],
                                "dial_url": f"{_DIAL_BASE}/Real/{section}/{row['property_id']}" if row["property_id"] else None,
                                "fetched_at": row["fetched_at"].isoformat(),
                            }
        except Exception as e:
            print(f"[dial_section] cache read error: {e}")

    # Live fetch
    property_id = _dial_resolve_property_id(taxlot)
    if not property_id:
        return {"found": False, "reason": f"Could not resolve property_id for taxlot {taxlot}", "section": section}

    section_url = f"{_DIAL_BASE}/Real/{section}/{property_id}"
    try:
        with _httpx.Client(follow_redirects=True, timeout=20.0,
                           headers={"User-Agent": _DIAL_USER_AGENT}) as c:
            r = c.get(section_url)
            r.raise_for_status()
            html = r.text
    except Exception as e:
        return {"found": False, "reason": f"DIAL fetch error: {e}", "property_id": property_id, "dial_url": section_url}

    # Parse the infoTable into [{header: cell_value, ...}, ...]
    soup = _BS(html, "html.parser")
    table = soup.find("table", class_="infoTable")
    rows = []
    if table:
        # Headers: collect from <th> elements (skip if no <td> in same row, those are header rows)
        header_cells = []
        for tr in table.find_all("tr"):
            ths = tr.find_all("th")
            tds = tr.find_all("td")
            if ths and not tds:
                # Pure header row — extract header text, stripping any nested <span> tooltips
                header_cells = [_re.sub(r"\s+", " ", th.get_text(separator=" ", strip=True)).strip() for th in ths]
                continue
            if tds:
                # Data row — pair cells with headers
                values = []
                links = []
                for td in tds:
                    val = _re.sub(r"\s+", " ", td.get_text(separator=" ", strip=True)).strip()
                    values.append(val)
                    a = td.find("a", href=True)
                    if a:
                        href = a["href"]
                        if href.startswith("/"):
                            href = _DIAL_BASE + href
                        links.append(href)
                row_dict = {}
                for i, val in enumerate(values):
                    key = header_cells[i] if i < len(header_cells) else f"col_{i}"
                    row_dict[key] = val
                if links:
                    row_dict["_links"] = links
                rows.append(row_dict)

    # Cache write
    try:
        with _conn() as cn:
            with cn.cursor() as cur:
                cur.execute(
                    """INSERT INTO dial_section_cache (taxlot, section, property_id, rows_json, row_count, fetched_at, last_error)
                       VALUES (%s, %s, %s, %s::jsonb, %s, NOW(), NULL)
                       ON CONFLICT (taxlot, section) DO UPDATE SET
                           property_id = EXCLUDED.property_id,
                           rows_json = EXCLUDED.rows_json,
                           row_count = EXCLUDED.row_count,
                           fetched_at = NOW(),
                           last_error = NULL""",
                    (taxlot, section, property_id, _json.dumps(rows), len(rows)),
                )
                cn.commit()
    except Exception as e:
        print(f"[dial_section] cache write error: {e}")

    return {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "section": section,
        "property_id": property_id,
        "row_count": len(rows),
        "rows": rows,
        "dial_url": section_url,
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def fetch_dial_sales(taxlot: str, force_refresh: bool = False) -> dict:
    """Sales history for a Deschutes parcel. Returns sale_date, seller, buyer, sale_amount per row."""
    return _dial_fetch_section(taxlot, "Sales", force_refresh)


def fetch_dial_valuation(taxlot: str, force_refresh: bool = False) -> dict:
    """Multi-year assessed value history for a Deschutes parcel."""
    return _dial_fetch_section(taxlot, "Valuation", force_refresh)


def fetch_dial_dev_docs(taxlot: str, force_refresh: bool = False) -> dict:
    """Development documents on file: easements, planning files, recorded encumbrances."""
    return _dial_fetch_section(taxlot, "DevelopmentDocs", force_refresh)


# =============================================================================
# YAKOV OMNISCIENCE — handoff and commit queries (DOCTRINE-NEVSKY-OMNISCIENCE-01)
# =============================================================================

def query_yakov_handoffs(
    hours_back: int | None = None,
    instance: str | None = None,
    only_high_impact: bool = False,
    only_with_tags: list[str] | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Pull recent Yakov session handoffs from knowledge_store.

    Use cases:
        - "what did Yakov do today?" -> hours_back=24
        - "show me the last high-impact session" -> only_high_impact=True, limit=1
        - "what did terminal Yakov do this week?" -> hours_back=168, instance="yakov-droplet"
    """
    where = ["'yakov' = ANY(tags)", "'handoff' = ANY(tags)"]
    params: list = []

    if hours_back is not None and hours_back > 0:
        where.append("created_at >= NOW() - (%s || ' hours')::interval")
        params.append(str(hours_back))

    if instance:
        where.append("(metadata->>'instance' = %s OR %s = ANY(tags))")
        params.append(instance)
        params.append(f"instance:{instance}")

    if only_high_impact:
        where.append("'high_impact' = ANY(tags)")

    if only_with_tags:
        where.append("tags @> %s")
        params.append(list(only_with_tags))

    sql = f"""
        SELECT id, title, tags, created_at,
               metadata->>'instance' AS instance,
               metadata->>'summary' AS summary,
               metadata->'changes' AS changes,
               metadata->'commit_hashes' AS commit_hashes,
               metadata->'next_priorities' AS next_priorities,
               metadata->'doctrine_notes' AS doctrine_notes,
               (metadata->>'high_impact')::boolean AS high_impact
        FROM knowledge_store
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("query_yakov_handoffs error: %s", e)
        return []


def query_yakov_commits(hours_back: int | None = None, limit: int = 20) -> list[dict]:
    """
    Pull recent git commits ingested by the post-commit hook.

    Use cases:
        - "what did Yakov ship today?" -> hours_back=24
        - "show me the last 5 commits" -> limit=5
    """
    where = ["'yakov' = ANY(tags)", "'git_commit' = ANY(tags)"]
    params: list = []

    if hours_back is not None and hours_back > 0:
        where.append("created_at >= NOW() - (%s || ' hours')::interval")
        params.append(str(hours_back))

    sql = f"""
        SELECT id, title, created_at,
               metadata->>'commit_hash' AS commit_hash,
               metadata->>'author' AS author,
               metadata->>'subject' AS subject,
               metadata->>'branch' AS branch,
               metadata->'files_changed' AS files_changed
        FROM knowledge_store
        WHERE {" AND ".join(where)}
        ORDER BY created_at DESC
        LIMIT %s
    """
    params.append(limit)

    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error("query_yakov_commits error: %s", e)
        return []


# =============================================================================
# TERRA YIELD CALCULATOR — speculative residential lot-yield estimate.
# Per DOCTRINE-TERRA-ANALYSIS-TOOL-01: every output is speculative and carries
# the planning-office disclaimer. Calculator never asserts certainty.
# =============================================================================

_TERRA_FOOTER = (
    "TERRA estimate — actual yield subject to site conditions, plat approval, "
    "and jurisdictional review. Verify with the city/county planning office "
    "before underwriting."
)

_ZONE_TYPE_LBL = {
    1: "DESCHUTES COUNTY", 2: "CITY OF BEND", 3: "CITY OF REDMOND",
    4: "CITY OF SISTERS", 5: "ALFALFA", 6: "BLACK BUTTE RANCH",
    7: "BROTHERS", 8: "DESCHUTES JUNCTION", 9: "DESCHUTES RIVER WOODS",
    10: "HAMPTON", 11: "WIDGI CREEK / INN OF 7TH MOUNTAIN",
    12: "LA PINE", 13: "MILLICAN", 14: "SPRING RIVER", 15: "SUNRIVER",
    16: "TERREBONNE", 17: "TUMALO", 18: "WHISTLE STOP", 19: "WILD HUNT",
    20: "CITY OF LA PINE",
}
_COMMUNITY_TYPE_LBL = {
    1: "COUNTY", 2: "URBAN RESERVE AREA", 3: "URBAN GROWTH BOUNDARY",
    4: "URBAN UNINCORPORATED COMMUNITY", 5: "RURAL SERVICE CENTER",
    6: "RURAL COMMUNITY", 7: "RESORT COMMUNITY", 8: "RURAL COMMERCIAL",
}


def _live_query_zoning(lat: float, lon: float) -> dict | None:
    """Phase 0B fallback. Point-query the Deschutes County ArcGIS zoning layer
    when parcels_deschutes.zone_code is NULL. Returns decoded zone info or None.
    Slow path: 300-800ms per call. Tool layer caches the result.
    """
    import json as _json
    import urllib.request as _urlreq
    import urllib.parse as _urlparse

    geom = _json.dumps({"x": lon, "y": lat, "spatialReference": {"wkid": 4326}})
    qs = _urlparse.urlencode({
        "geometry": geom,
        "geometryType": "esriGeometryPoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "ZONE,ZONE_TYPE,COMMUNITY_TYPE,ORDINANCE",
        "returnGeometry": "false",
        "f": "pjson",
    })
    url = (
        "https://maps.deschutes.org/arcgis/rest/services/"
        "OpenData/LandFD/MapServer/3/query?" + qs
    )
    try:
        with _urlreq.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read())
    except Exception as e:
        logger.warning("live zoning query failed for (%s,%s): %s", lat, lon, e)
        return None
    feats = data.get("features") or []
    if not feats:
        return None
    a = feats[0].get("attributes") or {}
    return {
        "zone": a.get("ZONE"),
        "zone_type_int": a.get("ZONE_TYPE"),
        "community_type_int": a.get("COMMUNITY_TYPE"),
        "zone_jurisdiction": _ZONE_TYPE_LBL.get(a.get("ZONE_TYPE")),
        "community_class": _COMMUNITY_TYPE_LBL.get(a.get("COMMUNITY_TYPE")),
        "ordinance": (a.get("ORDINANCE") or "").strip() or None,
    }


def calculate_lot_yield(
    parcel_id: str,
    deduction_override: float | None = None,
    per_lot_value_override: float | None = None,
    target_zone_override: str | None = None,
) -> dict:
    """TERRA speculative residential yield estimate for a Deschutes County parcel.

    See DOCTRINE-TERRA-ANALYSIS-TOOL-01 and DOCTRINE-YIELD-HOOK-THEN-CLOSE-01.
    """
    SQFT_PER_ACRE = 43560
    deduction = deduction_override if deduction_override is not None else 0.25

    # 1. Pull parcel
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT id, taxlot, zone_code, zone_jurisdiction, zone_community_class,
                          shape_area,
                          ST_Y(ST_Centroid(geom)) AS lat,
                          ST_X(ST_Centroid(geom)) AS lon
                   FROM parcels_deschutes WHERE taxlot = %s""",
                (parcel_id,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"error": f"DB error pulling parcel: {e}", "footer": _TERRA_FOOTER}

    if not row:
        return {
            "parcel_id": parcel_id,
            "error": f"Parcel '{parcel_id}' not found in parcels_deschutes.",
            "footer": _TERRA_FOOTER,
        }

    parcel_db_id = row["id"]
    zone_code = row["zone_code"]
    zone_jurisdiction = row["zone_jurisdiction"]
    shape_area = float(row["shape_area"] or 0)
    gross_acres = round(shape_area / SQFT_PER_ACRE, 3)
    resolved_via = "bulk_join"

    # 2. Phase 0B fallback — live-query if no zoning on file
    if not zone_code:
        live = _live_query_zoning(row["lat"], row["lon"])
        if not live or not live.get("zone"):
            return {
                "parcel_id": parcel_id,
                "gross_acres": gross_acres,
                "error": (
                    "No zoning data on file for this parcel — likely federal, "
                    "state, or special-jurisdiction land. Yield calculation not applicable."
                ),
                "resolved_via": "fallback_no_match",
                "footer": _TERRA_FOOTER,
            }
        zone_code = live["zone"]
        zone_jurisdiction = live["zone_jurisdiction"]
        resolved_via = "fallback_live_query"
        # CACHE WRITE — backfilling reference data from the authoritative external
        # source (Deschutes County ArcGIS). Not business-state mutation; idempotent.
        # Same pattern as fetch_dial_permits writing to dial_permit_cache.
        try:
            with _conn() as conn, conn.cursor() as cur:
                cur.execute(
                    """UPDATE parcels_deschutes
                       SET zone_code = %s,
                           zone_jurisdiction = %s,
                           zone_community_class = %s,
                           zone_resolved_at = now()
                       WHERE id = %s AND zone_code IS NULL""",
                    (zone_code, zone_jurisdiction, live.get("community_class"), parcel_db_id),
                )
                conn.commit()
        except Exception as e:
            logger.warning("zoning cache backfill failed for %s: %s", parcel_id, e)

    # 3. Apply target_zone_override (what-if rezone analysis)
    effective_zone = (target_zone_override or zone_code).strip() if (target_zone_override or zone_code) else None
    effective_jurisdiction = zone_jurisdiction

    # 4. Look up zoning rules
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """SELECT zone_code, zone_label, is_residential, min_lot_sqft,
                          source_section, source_url, verified_at, notes
                   FROM zoning_lookup
                   WHERE jurisdiction = %s AND zone_code = %s""",
                (effective_jurisdiction, effective_zone),
            )
            lookup = cur.fetchone()
    except Exception as e:
        return {"error": f"DB error looking up zoning: {e}", "footer": _TERRA_FOOTER}

    if not lookup:
        return {
            "parcel_id": parcel_id,
            "gross_acres": gross_acres,
            "zone_code": effective_zone,
            "zone_jurisdiction": effective_jurisdiction,
            "resolved_via": resolved_via,
            "error": (
                f"Zone '{effective_zone}' for jurisdiction '{effective_jurisdiction}' "
                "not in lookup. Use target_zone_override or verify with planning office."
            ),
            "footer": _TERRA_FOOTER,
        }

    is_residential = bool(lookup["is_residential"])
    min_lot_sqft = lookup["min_lot_sqft"]
    verified = lookup["verified_at"] is not None

    # 5. Non-residential — informational, not an error
    if not is_residential or not min_lot_sqft:
        return {
            "parcel_id": parcel_id,
            "gross_acres": gross_acres,
            "zone_code": effective_zone,
            "zone_jurisdiction": effective_jurisdiction,
            "zone_label": lookup["zone_label"],
            "is_residential": False,
            "message": (
                f"Non-residential zone ({lookup['zone_label']}) — yield calculation "
                "not applicable. Brokers analyzing commercial/industrial parcels should "
                "evaluate FAR (floor-area ratio), parking ratio, and adjacent retail "
                "demand instead — TERRA does not yet model those metrics."
            ),
            "source_section": lookup["source_section"],
            "source_url": lookup["source_url"],
            "verified": verified,
            "resolved_via": resolved_via,
            "footer": _TERRA_FOOTER,
        }

    # 6. The math
    usable_acres = round(gross_acres * (1.0 - deduction), 3)
    usable_sqft = int(round(usable_acres * SQFT_PER_ACRE))
    lot_yield = max(0, usable_sqft // int(min_lot_sqft))

    out = {
        "parcel_id": parcel_id,
        "gross_acres": gross_acres,
        "usable_acres": usable_acres,
        "usable_sqft": usable_sqft,
        "min_lot_sqft": int(min_lot_sqft),
        "lot_yield": int(lot_yield),
        "zone_code": effective_zone,
        "zone_jurisdiction": effective_jurisdiction,
        "zone_label": lookup["zone_label"],
        "is_residential": True,
        "source_section": lookup["source_section"],
        "source_url": lookup["source_url"],
        "verified": verified,
        "resolved_via": resolved_via,
        "assumptions_used": {
            "deduction_pct": deduction,
            "deduction_default_used": deduction_override is None,
            "target_zone_overridden": target_zone_override is not None,
            "verified_against_ordinance": verified,
        },
        "footer": _TERRA_FOOTER,
    }

    # 7. Retail line — only if broker provided a per-lot value
    if per_lot_value_override is not None and per_lot_value_override > 0:
        out["per_lot_value"] = float(per_lot_value_override)
        out["gross_retail"] = round(lot_yield * float(per_lot_value_override), 2)
        out["per_lot_value_prompt_needed"] = False
    else:
        out["per_lot_value"] = None
        out["gross_retail"] = None
        out["per_lot_value_prompt_needed"] = True
        out["per_lot_value_prompt_text"] = (
            "Broker: what's your market value per finished lot in this area? "
            "TERRA needs your number — we don't pretend to know your market better than you do."
        )

    return out
