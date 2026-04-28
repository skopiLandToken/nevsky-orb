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



def lookup_parcel_by_point(latitude: float, longitude: float) -> dict:
    """Spatial lookup: Deschutes County parcel containing a lat/lon point."""
    try:
        lat = float(latitude)
        lon = float(longitude)
    except (TypeError, ValueError):
        return {"found": False, "reason": "Invalid coordinates"}

    if not (43.5 <= lat <= 44.5) or not (-122.1 <= lon <= -119.8):
        return {"found": False, "reason": f"Point ({lat}, {lon}) outside Deschutes County. TERRA covers Deschutes only."}

    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT taxlot,
                       township || '-' || range_ || '-' || section AS section_id,
                       ROUND((shape_area / 43560.0)::numeric, 3) AS acres,
                       dial_url,
                       ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                       mapnumber
                FROM parcels_deschutes
                WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                LIMIT 1
                """,
                (lon, lat),
            )
            row = cur.fetchone()

    if not row:
        return {"found": False, "reason": f"No parcel contains point ({lat}, {lon}). May be road or right-of-way."}

    return {
        "found": True,
        "taxlot": row["taxlot"],
        "section_id": row["section_id"],
        "acres": float(row["acres"]) if row["acres"] is not None else None,
        "dial_url": row["dial_url"],
        "parcel_centroid": row["parcel_centroid"],
        "mapnumber": row["mapnumber"],
        "development_status": "unknown",
        "development_note": "Tier 2 permit watch not yet built. Permit-derived signals coming.",
        "queried_lat": lat,
        "queried_lon": lon,
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
