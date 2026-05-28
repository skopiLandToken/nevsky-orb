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
    "051": {  # Multnomah (FIPS 41-051) — Tier 1 Flagship, Portland metro
        "name": "Multnomah",
        # bbox: roughly Sauvie Island NW to Cascade Locks E, covers Portland + Gresham + unincorporated
        "bbox": (45.40, 45.80, -123.00, -121.40),
        "query": """
            SELECT taxlot,
                   COALESCE(map_id, township_range) AS section_id,
                   ROUND(size_acres::numeric, 3) AS acres,
                   -- Portland Maps search works for any parcel without captcha; deeper detail
                   -- pages exist but are address-dependent. Address search is the safe deeplink.
                   ('https://www.portlandmaps.com/search/?query=' ||
                       REPLACE(COALESCE(situs_address, taxlot), ' ', '+')) AS county_url,
                   COALESCE(map_id, township_range) AS map_number,
                   owner_name,
                   situs_address,
                   zone_code,
                   COALESCE(zone_code, 'unspecified') AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Multnomah'::text AS county_name,
                   '051'::text AS county_fips
            FROM parcels_multnomah
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "067": {  # Washington (FIPS 41-067) — Tier 1 Flagship, Portland metro westside
        "name": "Washington",
        # bbox: covers Beaverton + Hillsboro + Tigard + Forest Grove + unincorporated, padded
        # slightly outside the actual county boundary so edge parcels don't fall through.
        "bbox": (45.30, 45.85, -123.50, -122.60),
        "query": """
            SELECT taxlot,
                   map_number AS section_id,
                   ROUND((shape_area / 43560.0)::numeric, 3) AS acres,
                   -- A&T portal search-by-TLNO landing. Captcha-free landing page; the
                   -- viewstate POST to resolve TLNO → R-number is the IN FLIGHT piece.
                   ('https://washcotax.co.washington.or.us/Property-Search-Result/searchtext='
                       || taxlot) AS county_url,
                   map_number,
                   -- Washington L1 ships only TLNO + MAPNO + geom. Owner, situs, zoning
                   -- are all behind the A&T portal / Intermap zoning layer and resolved
                   -- on-demand by fetch_wash_assessment.
                   NULL::text AS owner_name,
                   NULL::text AS situs_address,
                   NULL::text AS zone_code,
                   NULL::text AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Washington'::text AS county_name,
                   '067'::text AS county_fips
            FROM parcels_washington
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "047": {  # Marion (FIPS 41-047) — Tier 1 Flagship, Salem state capital
        "name": "Marion",
        # bbox: covers Salem + Keizer + Stayton + Aumsville + Silverton + Mt Angel + unincorporated.
        # Marion sits south of Multnomah/Clackamas, north of Linn; padded slightly.
        "bbox": (44.65, 45.45, -123.30, -122.20),
        "query": """
            SELECT taxlot,
                   LEFT(taxlot, 6) AS section_id,
                   ROUND(acres::numeric, 3) AS acres,
                   reflink AS county_url,
                   LEFT(taxlot, 6) AS map_number,
                   owner_name,
                   situs_address,
                   zone_code,
                   COALESCE(zone_code || ' / ' || zone_auth, zone_code) AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Marion'::text AS county_name,
                   '047'::text AS county_fips
            FROM parcels_marion
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "039": {  # Lane (FIPS 41-039) — Tier 1 Flagship, Eugene/Springfield metro
        "name": "Lane",
        # bbox: Lane is one of Oregon's largest counties — Pacific coast (Florence)
        # east to the Cascade crest, with the Eugene/Springfield core in the western
        # interior. Bbox padded so coastal + crest-edge parcels don't fall through.
        "bbox": (43.40, 44.40, -124.30, -121.75),
        "query": """
            SELECT taxlot,
                   taxmap_number AS section_id,
                   ROUND(COALESCE(legal_area_acres, estimated_area_acres)::numeric, 3) AS acres,
                   -- rlid_link is the canonical per-parcel deep link the LGDC service
                   -- expects callers to surface. RLID full reports are subscription-gated,
                   -- but the URL lands the user on the property summary regardless.
                   -- Fallback: A&T public lookup at apps.lanecounty.org if rlid_link is null.
                   COALESCE(rlid_link,
                            'https://apps.lanecounty.org/PropertyAccountInformation/?searchType=MapTaxlot&maptaxlot=' || taxlot
                   ) AS county_url,
                   taxmap_number AS map_number,
                   reference_owner_name AS owner_name,
                   reference_full_address AS situs_address,
                   base_zone_code AS zone_code,
                   COALESCE(base_zone_code || ' / ' || plan_designation_code, base_zone_code) AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Lane'::text AS county_name,
                   '039'::text AS county_fips
            FROM parcels_lane
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "005": {  # Clackamas (FIPS 41-005) — Tier 1 Flagship, Portland metro south
        "name": "Clackamas",
        # bbox: covers Oregon City + Lake Oswego + West Linn + Milwaukie +
        # Happy Valley + Wilsonville (shared east strip with Washington) +
        # unincorporated south to the Cascade foothills. Padded slightly.
        "bbox": (45.05, 45.55, -123.10, -121.65),
        "query": """
            SELECT taxlot,
                   parcel_number AS section_id,
                   -- Source shape_area is Web Mercator m² (latitude-distorted).
                   -- Compute true acres geodesically: ST_Area(::geography) is m²
                   -- on the WGS84 spheroid; 4046.8564224 m² per acre.
                   ROUND((ST_Area(geom::geography) / 4046.8564224)::numeric, 3) AS acres,
                   -- ascendweb (A&T portal) account search landing. PARCEL_NUMBER
                   -- is the 8-char account # the portal expects in the Account
                   -- Number field. Captcha-free landing; the POST that resolves
                   -- to property detail is the __VIEWSTATE-gated piece.
                   ('http://ascendweb.clackamas.us/default.aspx?mAccount=' || parcel_number) AS county_url,
                   parcel_number AS map_number,
                   -- Clackamas L1 ships only TLNO + PARCEL_NUMBER + SITUS + TAXCODE
                   -- + geom (owner/valuation/sale/zoning explicitly stripped from
                   -- the public view by source). Resolved on demand by
                   -- fetch_clack_assessment.
                   NULL::text AS owner_name,
                   situs_address,
                   NULL::text AS zone_code,
                   NULL::text AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Clackamas'::text AS county_name,
                   '005'::text AS county_fips
            FROM parcels_clackamas
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "029": {  # Jackson (FIPS 41-029) — Tier 2 Major, Southern Oregon (Medford, Ashland, Central Point)
        "name": "Jackson",
        # bbox: Rogue Valley core + Cascade-crest east edge + California-border tail.
        "bbox": (41.95, 43.15, -123.50, -122.20),
        # Zoning is on a SEPARATE FeatureServer (jackson_zoning) — joined LATERALly
        # against the parcel centroid so the COUNTY_REGISTRY contract still passes
        # only one (lon, lat) parameter tuple.
        "query": """
            WITH hit AS (
                SELECT *
                FROM parcels_jackson
                WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                LIMIT 1
            )
            SELECT hit.taxlot,
                   COALESCE(hit.map_num, hit.map_number) AS section_id,
                   ROUND(hit.acreage::numeric, 3) AS acres,
                   'https://pdo.jacksoncountyor.gov/pdo/index.cfm' AS county_url,
                   COALESCE(hit.map_number, hit.map_num) AS map_number,
                   hit.owner_name,
                   hit.situs_address,
                   z.zone_code,
                   COALESCE(z.zone_code || ' / ' || z.zone_desc, z.zone_code) AS zone_label,
                   ST_AsText(ST_Centroid(hit.geom)) AS parcel_centroid,
                   'Jackson'::text AS county_name,
                   '029'::text AS county_fips
            FROM hit
            LEFT JOIN LATERAL (
                SELECT z.zone_code, z.zone_desc
                FROM jackson_zoning z
                WHERE ST_Intersects(z.geom, ST_Centroid(hit.geom))
                ORDER BY ST_Area(ST_Intersection(z.geom, hit.geom)) DESC NULLS LAST
                LIMIT 1
            ) z ON true
        """,
    },
    "043": {  # Linn (FIPS 41-043) — Tier 2 Major, mid-Willamette industrial corridor
        "name": "Linn",
        # bbox: covers Albany + Lebanon + Sweet Home + Brownsville + Harrisburg +
        # Halsey + Tangent + Scio + Mill City + Lyons + Idanha + unincorporated.
        # Linn sits south of Marion, north of Lane, west of Deschutes. Padded
        # slightly so the Cascade-edge towns (Idanha) don't fall through.
        "bbox": (44.20, 44.80, -123.30, -121.85),
        "query": """
            SELECT taxlot,
                   map_number AS section_id,
                   ROUND(COALESCE(taxlot_acre, calculated_acres, map_acres)::numeric, 3) AS acres,
                   -- Linn County's primary site is Cloudflare managed-challenge
                   -- gated; per-account portal URL not yet captured. Assessor home
                   -- is the canonical clickable until that lands. fetch_linn_assessment
                   -- returns the structured payload (joins pub_sales MapServer for
                   -- owner/situs/last-sale snapshot).
                   'https://www.linncountyor.gov/assessor' AS county_url,
                   map_number,
                   -- Linn L1 ships only ORMAP polygon attributes (no owner/situs
                   -- inline). Owner + situs are resolved on demand by
                   -- fetch_linn_assessment via pub_sales MapServer.
                   NULL::text AS owner_name,
                   NULL::text AS situs_address,
                   NULL::text AS zone_code,
                   NULL::text AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Linn'::text AS county_name,
                   '043'::text AS county_fips
            FROM parcels_linn
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    "003": {  # Benton (FIPS 41-003) — Tier 2 Major, Corvallis / Oregon State University
        "name": "Benton",
        # bbox: Benton spans the Coast Range east shoulder (Marys Peak) through
        # the western Willamette Valley to the river — Corvallis + Philomath +
        # Adair Village + Monroe + unincorporated. Padded slightly.
        "bbox": (44.20, 44.80, -123.85, -122.95),
        # Benton's TaxlotOwners service is owner-exploded — same polygon repeats
        # 1..N times once per Party_Name (e.g., trust co-trustees yield 2-3 rows
        # with identical Account_Num + ORTaxlot + geom). The CTE picks one row
        # via ST_Contains/LIMIT 1; the owner_name subquery re-aggregates all
        # Party_Name values for that ORTaxlot so a multi-trustee parcel shows
        # all of them. Source ships no native acres/zoning — acres computed
        # geodesically; zoning left NULL until L2 zoning fetcher lands.
        "query": """
            WITH hit AS (
                SELECT geom, ortaxlot, maptaxlot, account_num,
                       situs_addr1, situs_city, map_number
                FROM parcels_benton
                WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                LIMIT 1
            )
            SELECT h.ortaxlot AS taxlot,
                   h.map_number AS section_id,
                   ROUND((ST_Area(h.geom::geography) / 4046.8564224)::numeric, 3) AS acres,
                   'https://assessment.bentoncountyor.gov/property-account-search/' AS county_url,
                   h.map_number,
                   (SELECT STRING_AGG(DISTINCT party_name, '; ' ORDER BY party_name)
                      FROM parcels_benton b
                      WHERE b.ortaxlot = h.ortaxlot
                        AND b.party_name IS NOT NULL) AS owner_name,
                   CASE
                       WHEN h.situs_addr1 IS NULL OR h.situs_addr1 = 'UNASSIGNED' THEN NULL
                       ELSE h.situs_addr1 || COALESCE(', ' || h.situs_city, '')
                   END AS situs_address,
                   NULL::text AS zone_code,
                   NULL::text AS zone_label,
                   ST_AsText(ST_Centroid(h.geom)) AS parcel_centroid,
                   'Benton'::text AS county_name,
                   '003'::text AS county_fips
            FROM hit h
        """,
    },
    "053": {  # Polk (FIPS 41-053) — Tier 2 Major, Dallas / Independence / Monmouth / West Salem
        "name": "Polk",
        # bbox: Polk spans the west Willamette Valley from the Coast Range east
        # to the Willamette River (Salem's west bank). Dallas (county seat),
        # Independence, Monmouth, Falls City, Willamina (split with Yamhill),
        # plus all unincorporated rural land. Padded slightly.
        "bbox": (44.65, 45.10, -123.80, -123.00),
        # Polk's Assessor Taxlots service ships joined parcel+account attributes
        # in one row (Marion-style) — owner, mailing block, situs, last instrument,
        # property class, assessed values, account ID. So L1 covers most of what
        # the assessor portal would return; L2 = SQL pass-through, no scrape.
        # Acres source priority: taxlot_acres (polygon native, the assessor of
        # record value when set) → acct_acres (account-of-record) → geodesic
        # ST_Area (calc'd from polygon for edge cases where both are zero,
        # which happens on small city lots flagged with TaxlotFeet only).
        "query": """
            SELECT taxlot,
                   map_number AS section_id,
                   ROUND(
                       COALESCE(
                           NULLIF(taxlot_acres, 0),
                           NULLIF(acct_acres, 0),
                           ST_Area(geom::geography) / 4046.8564224
                       )::numeric,
                       3
                   ) AS acres,
                   'https://www.polkcountyor.gov/assessor' AS county_url,
                   map_number,
                   COALESCE(NULLIF(owner_line1, ''), NULLIF(agent_name, '')) AS owner_name,
                   CASE
                       WHEN site_add_nam IS NULL OR site_add_nam = '' THEN NULL
                       ELSE site_add_nam || COALESCE(', ' || NULLIF(site_add_cty, ''), '')
                   END AS situs_address,
                   NULL::text AS zone_code,
                   NULL::text AS zone_label,
                   ST_AsText(ST_Centroid(geom)) AS parcel_centroid,
                   'Polk'::text AS county_name,
                   '053'::text AS county_fips
            FROM parcels_polk
            WHERE ST_Contains(geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
            LIMIT 1
        """,
    },
    # Jefferson (031) lands here when its L1 table exists.
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
# CROOK Layer 2 Fetchers — assessment + permits
# =============================================================================
# Two truths discovered during this build (worth keeping for Nevsky):
#
#   1. Crook tax cards (gis.crookcountyor.gov/taxcards/{taxlot}.pdf) are scanned
#      image PDFs from 2013 — CCITTFaxDecode TIFF Group 4, zero embedded text.
#      Structured valuation / tax history extraction would require tesseract OCR
#      on aged scans, which is a heavy dependency and noisy output. So this
#      fetcher returns the rich inline data we already hold in parcels_crook
#      (owner, situs, account, prop_class, zoning, acreage) PLUS verified deep
#      links to the PDF and the PSO recorder — and flags the missing structured
#      fields as IN FLIGHT honestly rather than fabricating extraction.
#
#   2. Oregon ePermitting (aca-oregon.accela.com) is an ASP.NET viewstate-heavy
#      app. A bare POST with __VIEWSTATE/__VIEWSTATEGENERATOR + parcel field
#      returns the SAME 58 KB page no matter what parcel we submit — server-side
#      validation silently rejects without the full ClientState payload. Real
#      scraping needs either Playwright or a byte-for-byte browser capture. Until
#      then, fetch_county_permits returns a verified deep link to the Accela
#      search page pre-filled with the parcel and labels the fetched list IN
#      FLIGHT — much better than a scraper that silently returns 0 permits for
#      every parcel.
# =============================================================================

_CROOK_CACHE_TTL_HOURS = 24
_ACA_OREGON_BASE = "https://aca-oregon.accela.com/oregon"


def fetch_crook_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Crook assessment + ownership snapshot for a taxlot.

    Layer 1 inline data (owner, situs, account, zoning, acreage) is the source
    of truth and is always fresh. Structured valuation / tax-history / sales
    fields are NOT available without OCR on the legacy scanned tax cards; those
    are flagged in_flight and the user gets a direct PDF link instead.

    Cache exists primarily to hold pdf_etag for future cheap re-checks and to
    keep a consistent fetched_at timestamp visible in the Sophia tool output.

    Returns: {found, taxlot, county_fips, snapshot{owner, situs, account, zoning, acreage},
              deep_links{tax_card_pdf, tax_map_pdf, pso_recorder}, in_flight[],
              fetched_at, from_cache, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, pdf_url, parsed_json, fetched_at, last_error "
                    "FROM crook_tax_card_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached and cached["parsed_json"]:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_CROOK_CACHE_TTL_HOURS):
                        payload = cached["parsed_json"]
                        payload["from_cache"] = True
                        payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                        return payload
        except Exception as e:
            print(f"[crook_assessment] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT taxlot, county_fips, owner_name, situs_address, mailing_address,
                       account, prop_class, pc_desc, tax_code_area,
                       sub_name, sub_block, sub_lot,
                       assessed_acres, gis_acres,
                       zone_code, zone_desc, map_number,
                       pats_url, tax_card_url, tax_map_url, pso_url
                FROM parcels_crook WHERE taxlot = %s LIMIT 1
                """,
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_crook", "taxlot": taxlot}

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_crook L1 inline + Crook County GIS / PSO deep links",
        "snapshot": {
            "owner": {
                "primary": row["owner_name"],
                "mailing_address": row["mailing_address"],
            },
            "situs": {
                "address": row["situs_address"],
            },
            "account": {
                "number": row["account"],
                "prop_class": row["prop_class"],
                "pc_desc": row["pc_desc"],
                "tax_code_area": row["tax_code_area"],
            },
            "subdivision": {
                "name": row["sub_name"],
                "block": row["sub_block"],
                "lot": row["sub_lot"],
            } if row["sub_name"] else None,
            "acreage": {
                "assessed": float(row["assessed_acres"]) if row["assessed_acres"] is not None else None,
                "gis":      float(row["gis_acres"])      if row["gis_acres"]      is not None else None,
            },
            "zoning": {
                "code":  row["zone_code"],
                "label": row["zone_desc"],
            },
            "map_number": row["map_number"],
        },
        "deep_links": {
            "tax_card_pdf":  row["tax_card_url"],   # legacy scanned PDF, image-only
            "tax_map_pdf":   row["tax_map_url"],
            "pso_recorder":  row["pso_url"] or row["pats_url"],
        },
        "in_flight": [
            "Structured valuation (real-market / assessed / taxable) — Crook tax cards are scanned image PDFs from 2013; OCR pipeline queued.",
            "Multi-year tax payment history — same OCR blocker.",
            "Sales chain — PSO recorder is Blazor Server (JS-required); needs Playwright or alt source. Deep link above lets the user pull it themselves.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO crook_tax_card_cache (taxlot, pdf_url, parsed_json, fetched_at, last_error)
                   VALUES (%s, %s, %s::jsonb, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       pdf_url = EXCLUDED.pdf_url,
                       parsed_json = EXCLUDED.parsed_json,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, row["tax_card_url"], _json.dumps(payload)),
            )
            cn.commit()
    except Exception as e:
        print(f"[crook_assessment] cache write error: {e}")

    return payload


# Counties known to participate in Oregon's state ePermitting (Accela ACA).
# Source: oregon.gov BCD jurisdiction lookup, cross-checked with county building
# department pages. This set drives the "in_flight vs not_applicable" branch in
# fetch_county_permits — counties NOT in this set get a structured response
# saying the state portal won't have their permits, look elsewhere.
_OREGON_EPERMITTING_FIPS = {
    "003",  # Benton       — confirmed via cd.bentoncountyor.gov/electronic-permitting
            #                links directly to aca-oregon.accela.com/oregon as the county e-permitting surface.
    "013",  # Crook        — confirmed via co.crook.or.us/commdev FAQ
    "031",  # Jefferson    — historically participates; verify per-build
    "039",  # Lane         — partial; participates for some jurisdictions
    "043",  # Linn         — partial; Albany runs its OWN portal at permits.albanyoregon.gov,
            #                so the Albany-jurisdiction branch in fetch_linn_permits routes
            #                away from state Accela. Lebanon / Sweet Home / Brownsville /
            #                smaller cities / unincorporated Linn ride the state portal as a
            #                deep-link-only landing (IN FLIGHT verification per jurisdiction).
    "047",  # Marion       — confirmed via the Marion County PublicWorksPermits GIS
            #                service (gis.co.marion.or.us/.../PublicWorksPermits) whose
            #                description states permits are "as entered in Accela"; the
            #                aca-oregon.accela.com /oregon module exposes Marion permits.
    # Deschutes (017) does NOT participate — uses DIAL natively. Multnomah (051)
    # has its own permit system (Portland Maps for COP parcels). Add new FIPS
    # codes here only after confirming via the BCD jurisdiction lookup.
}


def fetch_county_permits(taxlot: str, county_fips: str, force_refresh: bool = False) -> dict:
    """Permit search for a parcel via Oregon ePermitting (Accela ACA).

    Reusable across every county that participates in Oregon's state ePermitting
    portal — pass the county_fips and the function does the right thing. Counties
    not in _OREGON_EPERMITTING_FIPS get a structured "not_applicable" response
    routing the caller to the county-native system instead.

    NOTE — current implementation: returns a verified deep link to the Accela
    search page pre-filled with the taxlot, plus an in_flight notice. Real
    scrape is gated on either (a) Playwright in the container or (b) a fully
    captured browser viewstate payload. Cache table is ready and the function
    contract is stable — when the scrape lands, this function changes internally
    but the return shape doesn't.

    Returns: {found, taxlot, county_fips, status, permit_count, permits,
              deep_links{accela_search}, in_flight[], fetched_at, from_cache}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    if not county_fips or not isinstance(county_fips, str):
        return {"found": False, "reason": "Invalid county_fips"}
    taxlot = taxlot.strip().upper()
    county_fips = county_fips.strip()

    if county_fips not in _OREGON_EPERMITTING_FIPS:
        return {
            "found": False,
            "status": "not_applicable",
            "taxlot": taxlot,
            "county_fips": county_fips,
            "reason": (
                f"County FIPS 41-{county_fips} does not participate in Oregon's state "
                "ePermitting portal. Use the county-native permit system instead "
                "(Deschutes → DIAL via fetch_dial_permits; Multnomah → Portland Maps "
                "via fetch_multnomah_permits)."
            ),
        }

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT permits_json, permit_count, aca_search_url, fetched_at, last_error "
                    "FROM oregon_permits_cache WHERE taxlot = %s AND county_fips = %s",
                    (taxlot, county_fips),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_CROOK_CACHE_TTL_HOURS):
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "county_fips": county_fips,
                            "status": "deep_link_only",
                            "permit_count": cached["permit_count"],
                            "permits": cached["permits_json"] or [],
                            "deep_links": {"accela_search": cached["aca_search_url"]},
                            "in_flight": [
                                "Server-side scrape of the Accela result page — gated on viewstate capture or Playwright.",
                            ],
                            "fetched_at": cached["fetched_at"].isoformat(),
                        }
        except Exception as e:
            print(f"[county_permits] cache read error: {e}")

    # Deep link the caller can click through to a pre-filled Accela search.
    # GET /oregon/Cap/CapHome.aspx?module=Building works without auth; the user
    # then pastes the taxlot into the parcel field. We build the URL once and
    # cache the structured "found but not scraped" response.
    search_url = f"{_ACA_OREGON_BASE}/Cap/CapHome.aspx?module=Building"
    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "county_fips": county_fips,
        "status": "deep_link_only",
        "permit_count": 0,
        "permits": [],
        "deep_links": {"accela_search": search_url},
        "in_flight": [
            "Server-side scrape of the Accela result page — gated on viewstate capture or Playwright (a bare-bones POST returns the unfiltered form template, server-side validation silently rejects without the full ClientState payload).",
            "Until then: clicking the deep link drops the user on Accela's search page where pasting the taxlot returns the real list in one search submit.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO oregon_permits_cache (taxlot, county_fips, permits_json, permit_count, aca_search_url, fetched_at, last_error)
                   VALUES (%s, %s, '[]'::jsonb, 0, %s, NOW(), NULL)
                   ON CONFLICT (taxlot, county_fips) DO UPDATE SET
                       aca_search_url = EXCLUDED.aca_search_url,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, county_fips, search_url),
            )
            cn.commit()
    except Exception as e:
        print(f"[county_permits] cache write error: {e}")

    return payload


def fetch_crook_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Crook (FIPS 013)."""
    return fetch_county_permits(taxlot, "013", force_refresh)


# =============================================================================
# MARION Layer 2 + Layer 3 Fetchers
# =============================================================================
# Design choice (2026-05-27, s1 Yindo session): Marion's bulk Parcels MapServer
# already exposes ownership, situs, current valuation (RMV land/imp/total + assessed),
# zoning (code + authority + URL), latest deed snapshot (instrument number/type/date,
# sale price), property class, building metrics, and verified deep links to MCASR
# and the tax-map PDF. That's everything the Layer-2 fetcher would normally pull
# from an external portal — and it's present in parcels_marion as of the most
# recent ingest. So fetch_marion_assessment and fetch_marion_records are SQL
# pass-throughs over L1 inline data with NO external HTTP fetch (and therefore no
# L2 cache table — L1 itself is the cache, refreshed by the daily delta job).
#
# What's IN FLIGHT:
#   - Multi-year tax payment history → MCASR PropertySummary is ASP.NET WebForms
#     with __VIEWSTATE-gated data on the initial GET. Same scrape-gating issue we
#     hit on the Crook PSO portal. Deep link to PropertySummary is returned for
#     the user to click through.
#   - Full deed chain (multi-instrument history) → Marion Clerk Recordings public
#     portal URL not yet located; only the latest instrument is in L1. Brief listed
#     the Clerk recordings portal as "verify during recon" and we surface a
#     pointer to the Clerk's office page instead.
#
# Permits (Layer 3): Marion participates in Oregon ePermitting via the same
# aca-oregon.accela.com portal as Crook (confirmed via the Marion PublicWorksPermits
# GIS service whose own metadata names Accela as the source-of-record). So Marion
# only needs the convenience wrapper around fetch_county_permits — no Marion-specific
# scraper code. The richer alternative is to spatially intersect parcels_marion.geom
# vs the public Public/PublicWorksPermits/MapServer/0 + /1 point layers, which is
# queued as the Layer-3 upgrade once a daily-poll permits worker lands.
# =============================================================================

_MARION_ASSESSOR_BASE = "https://mcasr.co.marion.or.us"
_MARION_CLERK_RECORDS_PAGE = "https://www.co.marion.or.us/CO/Clerk/Pages/Recordings.aspx"


def _marion_l1_row(taxlot: str):
    """Fetch the L1 row for a Marion taxlot. Returns dict or None."""
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT taxlot, county_fips, taxacct, alt_taxacct,
                       owner_name, owner_addr, owner_csz,
                       situs_address, situs_csz,
                       plat_name, block, lot, acres,
                       inst_num, inst_type, inst_date, sale_price,
                       zone_code, zone_auth, zone_web,
                       year_built, bldg_area, living_area,
                       prop_class, prp_cls_desc,
                       rmv_land, rmv_imp, rmv_total, assd_val,
                       tax_code, city, school_dist, fire_dist,
                       reflink, maplink,
                       ingested_at, last_modified
                FROM parcels_marion WHERE taxlot = %s LIMIT 1
                """,
                (taxlot,),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[marion_l1_row] DB error: {e}")
        return None


def fetch_marion_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Marion assessment + ownership snapshot for a taxlot.

    Pass-through over parcels_marion Layer 1 inline data (no external HTTP). Returns
    owner / situs / zoning / acreage / current valuation (real-market + assessed) /
    building metrics / property class + tax routing, plus verified deep links to
    MCASR PropertySummary and the assessor tax-map PDF.

    Multi-year tax payment history is IN FLIGHT — MCASR's PropertySummary.aspx is
    ASP.NET WebForms with __VIEWSTATE-gated rendering on the initial GET; the
    deep link returns the user to a single-tap landing page that surfaces the
    full history once they click through.

    Returns: {found, taxlot, county_fips, snapshot{owner, situs, zoning, valuation,
              account, acreage, building}, deep_links{mcasr_property, tax_map_pdf,
              zoning_authority}, in_flight[], fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _marion_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_marion", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_marion L1 inline (MC-ASR / MC-IT GIS) + MCASR deep links",
        "snapshot": {
            "owner": {
                "primary": row["owner_name"],
                "mailing_address": row["owner_addr"],
                "mailing_csz": row["owner_csz"],
            },
            "situs": {
                "address": row["situs_address"],
                "city_state_zip": row["situs_csz"],
            },
            "account": {
                "taxacct": row["taxacct"],
                "alt_taxacct": row["alt_taxacct"],
                "prop_class": row["prop_class"],
                "pc_desc": row["prp_cls_desc"],
                "tax_code": row["tax_code"],
                "city": row["city"],
                "school_district": row["school_dist"],
                "fire_district": row["fire_dist"],
            },
            "zoning": {
                "code": row["zone_code"],
                "authority": row["zone_auth"],
                "web": row["zone_web"],
            },
            "acreage": {
                "acres": float(row["acres"]) if row["acres"] is not None else None,
            },
            "valuation": {
                "rmv_land": row["rmv_land"],
                "rmv_improvement": row["rmv_imp"],
                "rmv_total": row["rmv_total"],
                "assessed_value": row["assd_val"],
            },
            "building": {
                "year_built": row["year_built"] if row["year_built"] else None,
                "building_area_sqft": row["bldg_area"],
                "living_area_sqft": row["living_area"],
            },
            "subdivision": {
                "plat_name": row["plat_name"],
                "block": row["block"],
                "lot": row["lot"],
            } if row["plat_name"] else None,
        },
        "deep_links": {
            "mcasr_property": row["reflink"],
            "tax_map_pdf":    row["maplink"],
            "zoning_authority": row["zone_web"],
        },
        "in_flight": [
            "Multi-year tax payment history — MCASR PropertySummary.aspx is ASP.NET WebForms with __VIEWSTATE-gated data on the initial GET. Deep link above lands the user one tap from the live history.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
        "l1_last_modified": row["last_modified"].isoformat() if row["last_modified"] else None,
    }


def fetch_marion_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Marion deed / recorded-instrument snapshot for a taxlot.

    Pass-through over parcels_marion Layer 1 inline data (no external HTTP). Returns
    the MOST-RECENT instrument-of-record on the parcel (number, type, date, sale
    price), the current owner of record, and a deep link to the Marion Clerk's
    Recordings page where the user can search the full document history.

    Full deed chain (multi-instrument history) is IN FLIGHT — Marion Clerk's
    public recordings portal URL is not yet located (the standalone
    recordings.co.marion.or.us / landmark.co.marion.or.us / land.co.marion.or.us
    DNS endpoints all 404 on direct probe; the Clerk's office page is the
    pointer the brief recommended until the portal URL is confirmed).

    Returns: {found, taxlot, county_fips, latest_instrument, owner_of_record,
              deep_links{clerk_recordings, mcasr_property}, in_flight[],
              fetched_at, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _marion_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_marion", "taxlot": taxlot}

    has_instrument = bool(row["inst_num"] or row["inst_date"] or row["sale_price"])

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_marion L1 inline (MC-ASR / MC-IT GIS) + Marion Clerk deep link",
        "latest_instrument": {
            "number": row["inst_num"] or None,
            "type":   row["inst_type"] or None,
            "date":   row["inst_date"].isoformat() if row["inst_date"] else None,
            "sale_price": row["sale_price"] if row["sale_price"] else None,
        } if has_instrument else None,
        "owner_of_record": {
            "primary": row["owner_name"],
            "mailing_address": row["owner_addr"],
            "mailing_csz": row["owner_csz"],
        },
        "deep_links": {
            "clerk_recordings": _MARION_CLERK_RECORDS_PAGE,
            "mcasr_property":   row["reflink"],
        },
        "in_flight": [
            "Multi-instrument deed chain — Marion Clerk's public recordings portal URL not yet located. Currently surfacing the Clerk office landing page; a single search on owner or property ID returns the full chain.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "l1_ingested_at": row["ingested_at"].isoformat() if row["ingested_at"] else None,
    }


def fetch_marion_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Convenience wrapper around fetch_county_permits for Marion (FIPS 047).

    Marion participates in Oregon's state ePermitting portal (aca-oregon.accela.com)
    — confirmed via the Marion PublicWorksPermits GIS service whose own metadata
    names Accela as the source-of-record. This wrapper delegates to the shared
    Accela handler with county_fips='047' pre-set.

    Future upgrade path: Marion ALSO publishes the same permit data as point
    geometry via Public/PublicWorksPermits/MapServer/{0,1}. A spatial-intersect
    fetcher against parcels_marion.geom would give us live per-parcel permit lists
    without any Accela scrape — queued as the Layer-3 upgrade and noted in KB_80.
    """
    return fetch_county_permits(taxlot, "047", force_refresh)


# =============================================================================
# MULTNOMAH Layer 2 + Layer 3 Fetchers
# =============================================================================
# Reality check (2026-05-27, s2 Yindo session): of the three Layer 2 sources
# named in YINDO_BRIEF_TERRA_MULTNOMAH.md, two are captcha-walled (MultcoPropTax
# behind a generic captcha, MultcoRecords behind Google reCAPTCHA v2) and the
# third (SAIL) is not in the public Multnomah Assessment & Taxation catalog —
# may have been retired or renamed. See KB_78 handoff.
#
# What works: the bulk taxlot service from Multnomah is unusually RICH — owner,
# situs, zoning, valuation, sale price/date, deed type/date/instrument, year
# built, acreage are all native in parcels_multnomah. That's most of what we'd
# fetch from PropTax anyway. So fetch_multco_proptax is a SQL pass-through over
# Layer 1 inline data; the time-series stuff (tax payment history) is IN FLIGHT.
#
# Portland city parcels (~80% of Multnomah) also have an excellent permits
# layer via Portland Maps' ArcGIS Hub — fetch_multnomah_permits hits the COP
# OpenData Building Permit Details FeatureServer directly.
# =============================================================================

_MULTCO_CACHE_TTL_HOURS = 24
# Portland Open Data — Residential Building Permits (Layer 89). Live and stable.
# Catalog entry for "Building Permit Details" (layer 1288) is stale — not present
# in the live MapServer as of 2026-05-27. Commercial + multi-family permits are
# IN FLIGHT (likely live under a different bureau MapServer; needs Iosif/Yindo
# follow-up to identify the unified all-permits endpoint).
_PDX_PERMITS_URL = (
    "https://www.portlandmaps.com/od/rest/services/"
    "COP_OpenData_PlanningDevelopment/MapServer/89/query"
)
_PDX_PERMITS_LAYER_NAME = "Residential Building Permits (Portland Open Data Layer 89)"


def fetch_multco_proptax(taxlot: str, force_refresh: bool = False) -> dict:
    """Multnomah property snapshot (PropTax-equivalent) for a taxlot.

    Built on parcels_multnomah Layer 1 inline data. The Multnomah taxlot service
    ships owner, situs, valuation, sale, and deed natively — no upstream MultcoPropTax
    call is required for the snapshot. Tax payment history + bill status are IN FLIGHT
    (MultcoPropTax is captcha-walled; paid Tyler API tier pending Iosif decision).

    Returns: {found, taxlot, propid, snapshot{owner,situs,valuation,sale,deed,zoning,improvements},
              in_flight[], fetched_at, from_cache, source_url}
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
                        "SELECT taxlot, propid, payload_json, fetched_at, last_error "
                        "FROM multco_proptax_cache WHERE taxlot = %s",
                        (taxlot,),
                    )
                    cached = cur.fetchone()
                    if cached:
                        age = _dt.now(_tz.utc) - cached["fetched_at"]
                        if age < _td(hours=_MULTCO_CACHE_TTL_HOURS) and cached["payload_json"]:
                            payload = cached["payload_json"]
                            payload["from_cache"] = True
                            payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                            return payload
        except Exception as e:
            print(f"[multco_proptax] cache read error: {e}")

    # Pull from Layer 1
    try:
        with _conn() as cn:
            with cn.cursor() as cur:
                cur.execute(
                    """
                    SELECT taxlot, propid, alt_account_num,
                           owner_name, owner_name_2,
                           mailing_address, mailing_city, mailing_state, mailing_zip,
                           situs_address, situs_city, situs_state, situs_zip,
                           zone_code, prop_class, prop_code, account_status, exemption,
                           deed_type, deed_date, inst_num,
                           sale_price, sale_date,
                           size_acres, size_sqft, imp_type, act_year_built, main_sqft, units,
                           roll_year, roll_land, roll_imp, roll_m50,
                           ingested_at
                    FROM parcels_multnomah WHERE taxlot = %s LIMIT 1
                    """,
                    (taxlot,),
                )
                row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_multnomah", "taxlot": taxlot}

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "propid": row["propid"],
        "alt_account_num": row["alt_account_num"],
        "snapshot": {
            "owner": {
                "primary": row["owner_name"],
                "secondary": row["owner_name_2"],
                "mailing_address": row["mailing_address"],
                "mailing_city": row["mailing_city"],
                "mailing_state": row["mailing_state"],
                "mailing_zip": row["mailing_zip"],
            },
            "situs": {
                "address": row["situs_address"],
                "city": row["situs_city"],
                "state": row["situs_state"],
                "zip": row["situs_zip"],
            },
            "valuation": {
                "roll_year": row["roll_year"],
                "roll_land": float(row["roll_land"]) if row["roll_land"] is not None else None,
                "roll_imp": float(row["roll_imp"]) if row["roll_imp"] is not None else None,
                "roll_m50_assessed": float(row["roll_m50"]) if row["roll_m50"] is not None else None,
            },
            "most_recent_sale": {
                "sale_price": float(row["sale_price"]) if row["sale_price"] is not None else None,
                "sale_date": row["sale_date"].isoformat() if row["sale_date"] else None,
            },
            "most_recent_deed": {
                "deed_type": row["deed_type"],
                "deed_date": row["deed_date"].isoformat() if row["deed_date"] else None,
                "instrument_number": row["inst_num"],
            },
            "zoning": {"zone_code": row["zone_code"]},
            "improvements": {
                "type": row["imp_type"],
                "year_built": row["act_year_built"],
                "main_sqft": row["main_sqft"],
                "units": row["units"],
            },
            "size": {
                "acres": float(row["size_acres"]) if row["size_acres"] is not None else None,
                "sqft": row["size_sqft"],
            },
            "account": {
                "prop_class": row["prop_class"],
                "prop_code": row["prop_code"],
                "status": row["account_status"],
                "exemption": row["exemption"],
            },
        },
        "in_flight": [
            "tax_payment_history (MultcoPropTax captcha-walled; paid Tyler API tier pending Iosif decision)",
            "current_bill_status (same blocker)",
        ],
        "source": "parcels_multnomah Layer 1 (bulk taxlot service)",
        "source_url": (
            f"https://multcoproptax.com/  (manual search — site requires captcha; "
            f"PropID for lookup: {row['propid']})"
        ),
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    # Cache write
    try:
        with _conn() as cn:
            with cn.cursor() as cur:
                cur.execute(
                    """INSERT INTO multco_proptax_cache (taxlot, propid, payload_json, fetched_at, last_error)
                       VALUES (%s, %s, %s::jsonb, NOW(), NULL)
                       ON CONFLICT (taxlot) DO UPDATE SET
                           propid = EXCLUDED.propid,
                           payload_json = EXCLUDED.payload_json,
                           fetched_at = NOW(),
                           last_error = NULL""",
                    (taxlot, row["propid"], _json.dumps(payload, default=str)),
                )
                cn.commit()
    except Exception as e:
        print(f"[multco_proptax] cache write error: {e}")

    return payload


def fetch_multco_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Multnomah recorded documents (deeds since 2002).

    IN FLIGHT — MultcoRecords.com is gated by Google reCAPTCHA v2 on the disclaimer.
    Best-available info comes from Layer 1: most-recent deed/instrument is inline.
    Full deed history requires either (a) paid subscription, (b) human-in-the-loop
    captcha solving, or (c) alternative source. Iosif decision pending.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    try:
        with _conn() as cn:
            with cn.cursor() as cur:
                cur.execute(
                    """SELECT taxlot, owner_name, deed_type, deed_date, inst_num, sale_date, sale_price
                       FROM parcels_multnomah WHERE taxlot = %s LIMIT 1""",
                    (taxlot,),
                )
                row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_multnomah", "taxlot": taxlot}

    return {
        "found": True,
        "in_flight": True,
        "taxlot": row["taxlot"],
        "best_available_from_layer_1": {
            "owner": row["owner_name"],
            "most_recent_deed": {
                "type": row["deed_type"],
                "date": row["deed_date"].isoformat() if row["deed_date"] else None,
                "instrument_number": row["inst_num"],
            },
            "most_recent_sale": {
                "date": row["sale_date"].isoformat() if row["sale_date"] else None,
                "price": float(row["sale_price"]) if row["sale_price"] is not None else None,
            },
        },
        "in_flight_reason": (
            "MultcoRecords.com (Digital Research Room) is gated by Google reCAPTCHA v2 "
            "on its disclaimer page. Full recorded-document index since 2002 requires "
            "either subscription access, human-in-the-loop captcha solving, or an alternative "
            "data source. Pending Iosif decision on which path to take."
        ),
        "source_url": "https://multcorecords.com/",
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def fetch_multco_sail(taxlot: str, force_refresh: bool = False) -> dict:
    """Multnomah Survey and Assessment Image Locator (cadastral imagery + plats).

    IN FLIGHT — As of 2026-05-27 the SAIL system is not listed in the public Multnomah
    County Assessment & Taxation catalog. May have been retired, renamed, or moved
    behind a different surface. Needs Iosif confirmation of current URL.
    """
    return {
        "found": False,
        "in_flight": True,
        "taxlot": (taxlot or "").strip().upper(),
        "in_flight_reason": (
            "SAIL (Survey and Assessment Image Locator) is not in the public Multnomah "
            "Assessment & Taxation catalog as of 2026-05-27. Need Iosif confirmation of "
            "current URL or whether SAIL has been retired in favor of another surface. "
            "Cadastral basics (map_id, township_range, assessor_map, legal_desc, tract_lot, "
            "block) are available in parcels_multnomah Layer 1."
        ),
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def fetch_multnomah_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Portland Maps Building Permit Details, spatial-intersected to the taxlot.

    Source: City of Portland Open Data — COP_OpenData_PlanningDevelopment MapServer
            layer 1288 "Building Permit Details" (no auth, public ArcGIS REST).
    Coverage: Portland city parcels (~80% of Multnomah). Gresham + unincorporated
    are IN FLIGHT (separate jurisdictions; need GreshamView + Multnomah direct).
    Strategy: get parcel geom from parcels_multnomah → query Portland's permit
    FeatureServer with spatial intersection by parcel envelope.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    # Get parcel geom + situs city for jurisdiction routing
    try:
        with _conn() as cn:
            with cn.cursor() as cur:
                cur.execute(
                    """SELECT taxlot, situs_city,
                              ST_AsGeoJSON(ST_Envelope(geom))::jsonb AS bbox_json,
                              ST_XMin(geom) AS xmin, ST_YMin(geom) AS ymin,
                              ST_XMax(geom) AS xmax, ST_YMax(geom) AS ymax
                       FROM parcels_multnomah WHERE taxlot = %s LIMIT 1""",
                    (taxlot,),
                )
                row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_multnomah", "taxlot": taxlot}

    situs_city = (row["situs_city"] or "").upper().strip()
    in_portland = situs_city in ("PORTLAND", "")  # blank city defaults to Portland routing (most common)

    # Gresham + unincorporated routing
    if not in_portland and situs_city != "PORTLAND":
        return {
            "found": True,
            "in_flight": True,
            "taxlot": taxlot,
            "situs_city": situs_city,
            "jurisdiction": "Gresham" if "GRESHAM" in situs_city else "Multnomah unincorporated / other",
            "permits": [],
            "in_flight_reason": (
                f"Parcel is in {situs_city}, not Portland city. Portland Maps permits "
                "feed only covers City of Portland jurisdiction. Gresham permits (GreshamView) "
                "and Multnomah unincorporated (county direct) fetchers are IN FLIGHT — "
                "build as a Phase 2 follow-up after Iosif validates the Portland-city path."
            ),
            "fetched_at": _dt.now(_tz.utc).isoformat(),
        }

    # Portland Maps ArcGIS REST query — spatial intersection with parcel envelope
    # Build esriGeometry envelope (xmin, ymin, xmax, ymax in WGS84)
    geom_param = _json.dumps({
        "xmin": float(row["xmin"]), "ymin": float(row["ymin"]),
        "xmax": float(row["xmax"]), "ymax": float(row["ymax"]),
        "spatialReference": {"wkid": 4326},
    })

    params = {
        "where": "1=1",
        "geometry": geom_param,
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "*",
        "returnGeometry": "false",
        "outSR": "4326",
        "f": "json",
        "resultRecordCount": "200",
    }

    try:
        with _httpx.Client(timeout=20.0, headers={"User-Agent": _DIAL_USER_AGENT}) as c:
            r = c.get(_PDX_PERMITS_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {
            "found": False,
            "reason": f"Portland Maps permits fetch error: {e}",
            "taxlot": taxlot,
        }

    if "error" in data:
        return {
            "found": False,
            "reason": f"Portland Maps API error: {data['error']}",
            "taxlot": taxlot,
        }

    features = data.get("features", []) or []
    permits = []
    for f in features:
        attrs = f.get("attributes", {}) or {}
        permits.append(attrs)

    return {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "situs_city": situs_city or "PORTLAND (inferred)",
        "jurisdiction": "City of Portland",
        "permit_count": len(permits),
        "permits": permits,
        "source": f"Portland Maps — {_PDX_PERMITS_LAYER_NAME}",
        "source_url": _PDX_PERMITS_URL,
        "coverage_note": (
            "RESIDENTIAL building permits only. Commercial + multi-family permits are "
            "IN FLIGHT — Portland's unified all-permits endpoint hasn't been located yet."
        ),
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


# =============================================================================
# WASHINGTON Layer 2 + Layer 3 Fetchers
# =============================================================================
# Reality check (2026-05-27, s3 Yindo session): Washington's public taxlot service
# is the OPPOSITE of Multnomah's — it ships ONLY TLNO/MAPNO/geometry. No inline
# owner / situs / valuation / sale / zoning. Rich data lives behind the A&T portal
# at washcotax.co.washington.or.us, which is DotNetNuke + ASP.NET WebForms with
# viewstate gating (same fight that lost s1's Accela scrape and s2's MultcoPropTax
# scrape).
#
# What works: (a) zoning lookup via Intermap/Land_Use/MapServer/8 (spatial query
# against the County Land Use Districts polygon layer — LUD field = zone code).
# (b) verified deep links to washcotax search-by-TLNO landing and the official
# A&T page. Owner / valuation / sale / tax history flagged IN FLIGHT pending
# viewstate capture or paid-tier authorization.
#
# Permits: Washington County uses its OWN Accela instance at
# permits.washingtoncountyor.gov, distinct from the state oregon.gov Accela
# (aca-oregon.accela.com) that handles Crook + Jefferson + Lane. So
# fetch_washington_permits is NOT a fetch_county_permits wrapper — it points
# to the county-direct portal with the same deep-link-only IN FLIGHT pattern.
# =============================================================================

_WASH_CACHE_TTL_HOURS = 24
_WASH_LAND_USE_URL = (
    "https://gispub.co.washington.or.us/server/rest/services/"
    "Intermap/Land_Use/MapServer/8/query"
)
_WASH_AT_BASE = "https://washcotax.co.washington.or.us"
_WASH_PERMITS_BASE = "https://permits.washingtoncountyor.gov"
_WASH_PERMITS_SEARCH = f"{_WASH_PERMITS_BASE}/CitizenAccess/Cap/CapHome.aspx?module=Building"


def _wash_lookup_zoning(taxlot: str) -> dict:
    """Spatial query against Intermap/Land_Use/MapServer/8 for the zone at the
    parcel's centroid. Returns {zone_code, source_url} or {zone_code: None}.

    Layer 8 ships only LUD (Plan/zone code, 10-char). No zoning description.
    Zone names map: AF-5/AF-10/AF-20 = Agriculture-Forest, R-* = residential
    densities, etc. — broker can interpret from the code; deep label lookup
    is deferred to a future zoning-dictionary join.
    """
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT ST_X(ST_Centroid(geom)) AS lon, ST_Y(ST_Centroid(geom)) AS lat
                   FROM parcels_washington WHERE taxlot = %s LIMIT 1""",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"zone_code": None, "error": f"DB: {e}"}

    if not row or row["lon"] is None:
        return {"zone_code": None, "error": "no centroid"}

    # Build esriGeometry point in WGS84; Land_Use service is in WKID 2913 but
    # accepts inSR=4326 + datum transform on the server side.
    geom_param = _json.dumps({
        "x": float(row["lon"]), "y": float(row["lat"]),
        "spatialReference": {"wkid": 4326},
    })
    params = {
        "where": "1=1",
        "geometry": geom_param,
        "geometryType": "esriGeometryPoint",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "LUD",
        "returnGeometry": "false",
        "f": "json",
    }
    try:
        with _httpx.Client(timeout=15.0, headers={"User-Agent": _DIAL_USER_AGENT}) as c:
            r = c.get(_WASH_LAND_USE_URL, params=params)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        return {"zone_code": None, "error": f"HTTP: {e}"}

    features = data.get("features", []) or []
    if not features:
        return {"zone_code": None, "source_url": _WASH_LAND_USE_URL}
    attrs = features[0].get("attributes", {}) or {}
    return {
        "zone_code": (attrs.get("LUD") or "").strip() or None,
        "source_url": _WASH_LAND_USE_URL,
    }


def fetch_wash_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Washington County assessment + property snapshot for a taxlot.

    Built on parcels_washington Layer 1 (geometry-derived acreage, taxlot, map_number)
    + a live spatial query against the Intermap Land Use layer for the zone code,
    plus verified deep links to the A&T portal. Owner, situs, valuation, sale, and
    tax payment history are IN FLIGHT pending viewstate-capture or paid-tier
    authorization for washcotax.co.washington.or.us.

    Returns: {found, taxlot, map_number, snapshot{size,zoning,jurisdiction_hints},
              deep_links{at_search,at_homepage}, in_flight[], fetched_at, from_cache}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    # Cache check
    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, r_number, payload_json, fetched_at, last_error "
                    "FROM wash_assessment_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_WASH_CACHE_TTL_HOURS) and cached["payload_json"]:
                        payload = cached["payload_json"]
                        payload["from_cache"] = True
                        payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                        return payload
        except Exception as e:
            print(f"[wash_assessment] cache read error: {e}")

    # Pull from Layer 1
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, map_number, taxlot_short,
                          ROUND((shape_area / 43560.0)::numeric, 3) AS acres,
                          ST_AsText(ST_Centroid(geom)) AS centroid
                   FROM parcels_washington WHERE taxlot = %s LIMIT 1""",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_washington", "taxlot": taxlot}

    # Live zoning query — non-blocking, return None on failure
    zoning = _wash_lookup_zoning(taxlot)

    at_search_url = f"{_WASH_AT_BASE}/Property-Search-Result/searchtext={taxlot}"
    at_home_url = "https://www.washingtoncountyor.gov/at"

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "map_number": row["map_number"],
        "taxlot_short": row["taxlot_short"],
        "snapshot": {
            "size": {
                "acres": float(row["acres"]) if row["acres"] is not None else None,
            },
            "zoning": {
                "zone_code": zoning.get("zone_code"),
                "source": "Intermap/Land_Use/MapServer/8 (live spatial query)" if zoning.get("zone_code") else None,
            },
            "location": {
                "centroid_wkt": row["centroid"],
            },
        },
        "deep_links": {
            "at_search_by_taxlot": at_search_url,
            "at_homepage": at_home_url,
        },
        "in_flight": [
            "owner_of_record + mailing_address (washcotax.co.washington.or.us is DNN + ASP.NET WebForms with viewstate-gated TLNO → R-number resolution).",
            "situs_address (same source).",
            "valuation snapshot (land/improvement/assessed) + multi-year certified values (same source).",
            "sales history + deed instruments (same source — also a 'Records' surface to confirm via fetch_wash_records).",
            "tax payment history + current bill status (same source).",
        ],
        "source": "parcels_washington Layer 1 + Intermap zoning layer",
        "source_url": at_search_url,
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    # Cache write
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO wash_assessment_cache (taxlot, r_number, payload_json, fetched_at, last_error)
                   VALUES (%s, NULL, %s::jsonb, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       payload_json = EXCLUDED.payload_json,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, _json.dumps(payload, default=str)),
            )
            cn.commit()
    except Exception as e:
        print(f"[wash_assessment] cache write error: {e}")

    return payload


def fetch_wash_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Washington County recorded documents (deeds, mortgages, liens).

    IN FLIGHT — Washington County Clerk recordings live on a separate surface
    that requires either subscription access or a paid tier. Best-available
    most-recent-instrument data is NOT inline in parcels_washington (unlike
    Multnomah where the bulk taxlot service ships deed_type/date/instrument).
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    # Confirm taxlot exists in L1 before claiming the IN FLIGHT
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "SELECT taxlot FROM parcels_washington WHERE taxlot = %s LIMIT 1",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_washington", "taxlot": taxlot}

    return {
        "found": True,
        "in_flight": True,
        "taxlot": row["taxlot"],
        "best_available_from_layer_1": {
            # Washington L1 ships nothing about deeds/owners — explicitly empty.
            "note": "Washington's bulk taxlot service does NOT ship deed/sale data inline (only TLNO + MAPNO + geom). Use fetch_wash_assessment for the size+zoning snapshot; deed chain is fully IN FLIGHT.",
        },
        "in_flight_reason": (
            "Washington County recorded documents (Clerk recordings) live on a separate "
            "surface that requires either paid-tier subscription, alternative source, "
            "or viewstate-captured washcotax search. Pending Iosif decision on which "
            "path to take — same blocker as fetch_multco_records."
        ),
        "deep_links": {
            "at_search_by_taxlot": f"{_WASH_AT_BASE}/Property-Search-Result/searchtext={taxlot}",
            "county_clerk_homepage": "https://www.washingtoncountyor.gov/recording",
        },
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def fetch_washington_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Building permits for a Washington County parcel.

    Washington runs its OWN Accela ACA instance at permits.washingtoncountyor.gov
    (distinct from the state oregon.gov Accela used by Crook/Jefferson/Lane via
    fetch_county_permits). Same deep-link-only IN FLIGHT pattern: a verified
    Accela search URL is returned, scrape upgrade gated on viewstate-capture
    or Playwright. Cache table is sized for the eventual real payload so the
    upgrade drops in without schema change.

    Jurisdiction routing: Beaverton / Hillsboro / Tigard cities have their own
    portals; permits.washingtoncountyor.gov covers unincorporated + many smaller
    jurisdictions. v1 surfaces the county Accela deep link; per-city portal
    routing is IN FLIGHT pending Iosif validation of the county-direct path.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    # Cache check
    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, jurisdiction, permits_json, permit_count, aca_search_url, "
                    "fetched_at, last_error FROM wash_permits_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_WASH_CACHE_TTL_HOURS):
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "jurisdiction": cached["jurisdiction"],
                            "status": "deep_link_only",
                            "permit_count": cached["permit_count"],
                            "permits": cached["permits_json"] or [],
                            "deep_links": {"accela_search": cached["aca_search_url"]},
                            "in_flight": [
                                "Server-side scrape of the county Accela result page — gated on viewstate capture or Playwright.",
                                "Per-city portal routing (Beaverton/Hillsboro/Tigard) for parcels inside those city limits.",
                            ],
                            "fetched_at": cached["fetched_at"].isoformat(),
                        }
        except Exception as e:
            print(f"[wash_permits] cache read error: {e}")

    # Confirm taxlot exists in L1
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "SELECT taxlot FROM parcels_washington WHERE taxlot = %s LIMIT 1",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_washington", "taxlot": taxlot}

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "jurisdiction": "Washington County (county-direct Accela)",
        "status": "deep_link_only",
        "permit_count": 0,
        "permits": [],
        "deep_links": {"accela_search": _WASH_PERMITS_SEARCH},
        "in_flight": [
            "Server-side scrape of the county Accela result page — gated on viewstate capture or Playwright (same blocker as fetch_county_permits for the state portal).",
            "Per-city portal routing — Beaverton / Hillsboro / Tigard run separate city permit systems; v1 only covers the county Accela.",
            "Until the scrape lands: clicking the deep link drops the user on the county's Accela search where pasting the taxlot returns the live list in one search submit.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    # Cache write
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO wash_permits_cache (taxlot, jurisdiction, permits_json, permit_count, aca_search_url, fetched_at, last_error)
                   VALUES (%s, %s, '[]'::jsonb, 0, %s, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       jurisdiction = EXCLUDED.jurisdiction,
                       aca_search_url = EXCLUDED.aca_search_url,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, payload["jurisdiction"], _WASH_PERMITS_SEARCH),
            )
            cn.commit()
    except Exception as e:
        print(f"[wash_permits] cache write error: {e}")

    return payload


# =============================================================================
# LANE Layer 2 + Layer 3 Fetchers (s2 Yindo session — 2026-05-27)
# =============================================================================
# Reality of the Lane integration (carries forward to Tier 2/3/4 builds):
#
#   1. parcels_lane Layer 1 ships the RICHEST inline attribute set of any Oregon
#      county TERRA has integrated — owner, situs, RMV land/impr/personal, AV,
#      taxable value, tax certified, zoning, plan designation, UGB code, fire
#      district, school district, census FIPS, and neighborhood association are
#      all native at L1. fetch_lane_assessment is therefore a SQL pass-through
#      over L1 — no upstream fetch is needed for the snapshot. Multi-year tax
#      payment history + bill status are IN FLIGHT (Lane A&T portal at
#      apps.lanecounty.org/PropertyAccountInformation/ is JS-rendered; account-
#      detail URL contract not captured this pass).
#
#   2. Lane records / deed chain / sales history live with the Lane County Clerk
#      recordings office, which has no public queryable API as of 2026-05-27.
#      RLID (rlid.org) consolidates these but full reports are subscription-gated.
#      fetch_lane_records returns the per-parcel rlid_link plus an IN FLIGHT
#      framing — no fake completion.
#
#   3. Lane County + Springfield participate in the Oregon state ePermitting
#      portal. Eugene runs its own PDD permitting — fetch_lane_permits branches
#      on planning_jurisdiction_code and routes Eugene parcels to PDD, everything
#      else to fetch_county_permits with county_fips='039'.
# =============================================================================

_LANE_CACHE_TTL_HOURS = 24
_LANE_AT_BASE = "https://apps.lanecounty.org/PropertyAccountInformation"
_LANE_RLID_BASE = "https://www.rlid.org"
_EUGENE_PDD_PERMITS = "https://www.eugene-or.gov/3360/Permits"


def fetch_lane_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Lane assessment + ownership + valuation snapshot for a taxlot.

    Layer 1 inline data (Lane LGDC service ships owner, situs, RMV, AV, taxable,
    tax certified, zoning, plan designation, jurisdiction, districts) is the
    source of truth. Tax payment history is IN FLIGHT (Lane A&T portal is
    JS-rendered; account-detail URL contract not captured).
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, account_number, payload_json, fetched_at, last_error "
                    "FROM lane_assessment_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached and cached["payload_json"]:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_LANE_CACHE_TTL_HOURS):
                        payload = cached["payload_json"]
                        payload["from_cache"] = True
                        payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                        return payload
        except Exception as e:
            print(f"[lane_assessment] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT taxlot, county_fips, maptaxlot_display, taxmap_number,
                       reference_account_number,
                       reference_owner_name, owner_count,
                       reference_full_address, address_count,
                       tax_code_area, has_tax_code_split,
                       total_real_market_value, total_real_market_value_land,
                       total_real_market_value_impr, total_real_market_value_pers,
                       total_assessed_value, total_taxable_value, total_amount_exempted,
                       total_tax_amount_certified, exemption_count, reference_exemption_type,
                       property_class_code, property_class_name,
                       statistical_class_code, statistical_class_name,
                       detailed_land_use_name, general_land_use_name, land_use_class,
                       base_zone_code, plan_designation_code,
                       planning_jurisdiction_code, incorporated_city_code,
                       urban_growth_boundary_code, urban_reserve_code,
                       improvement_count, reference_imprvmnt_type, reference_imprvmnt_year_built,
                       total_impr_finished_area_sqft, total_impr_base_area_sqft, structure_count,
                       school_district_code, fire_protection_provider_code,
                       neighborhood_association_name, market_area_name,
                       census_tract_fips_code, census_block_fips_code,
                       legal_area_acres, estimated_area_acres,
                       reference_plat_name,
                       rlid_link
                FROM parcels_lane WHERE taxlot = %s LIMIT 1
                """,
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_lane", "taxlot": taxlot}

    rlid_link = row["rlid_link"]
    at_search_url = f"{_LANE_AT_BASE}/?searchType=MapTaxlot&maptaxlot={taxlot}"

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "maptaxlot_display": row["maptaxlot_display"],
        "account_number": row["reference_account_number"],
        "county_fips": row["county_fips"],
        "source": "parcels_lane L1 inline (LCOG/LGDC Taxlot__Public service)",
        "snapshot": {
            "owner": {
                "primary": row["reference_owner_name"],
                "owner_count": row["owner_count"],
            },
            "situs": {
                "address": row["reference_full_address"],
                "address_count": row["address_count"],
            },
            "valuation": {
                "real_market_total": row["total_real_market_value"],
                "real_market_land": row["total_real_market_value_land"],
                "real_market_improvements": row["total_real_market_value_impr"],
                "real_market_personal": row["total_real_market_value_pers"],
                "assessed_value": row["total_assessed_value"],
                "taxable_value": row["total_taxable_value"],
                "amount_exempted": row["total_amount_exempted"],
                "tax_certified_annual": float(row["total_tax_amount_certified"]) if row["total_tax_amount_certified"] is not None else None,
                "exemption_count": row["exemption_count"],
                "exemption_type": row["reference_exemption_type"],
            },
            "taxation": {
                "tax_code_area": row["tax_code_area"],
                "has_tax_code_split": row["has_tax_code_split"],
            },
            "classification": {
                "property_class_code": row["property_class_code"],
                "property_class_name": row["property_class_name"],
                "statistical_class_code": row["statistical_class_code"],
                "statistical_class_name": row["statistical_class_name"],
                "detailed_land_use": row["detailed_land_use_name"],
                "general_land_use": row["general_land_use_name"],
                "land_use_class": row["land_use_class"],
            },
            "zoning": {
                "code": row["base_zone_code"],
                "plan_designation": row["plan_designation_code"],
                "label": (
                    f"{row['base_zone_code']} / {row['plan_designation_code']}"
                    if row["base_zone_code"] and row["plan_designation_code"]
                    else row["base_zone_code"]
                ),
            },
            "planning": {
                "jurisdiction_code": row["planning_jurisdiction_code"],
                "incorporated_city": row["incorporated_city_code"],
                "urban_growth_boundary": row["urban_growth_boundary_code"],
                "urban_reserve": row["urban_reserve_code"],
                "market_area": row["market_area_name"],
                "neighborhood": row["neighborhood_association_name"],
                "plat": row["reference_plat_name"],
            },
            "improvements": {
                "improvement_count": row["improvement_count"],
                "primary_type": row["reference_imprvmnt_type"],
                "year_built": row["reference_imprvmnt_year_built"],
                "finished_area_sqft": row["total_impr_finished_area_sqft"],
                "base_area_sqft": row["total_impr_base_area_sqft"],
                "structure_count": row["structure_count"],
            },
            "districts": {
                "school_district": row["school_district_code"],
                "fire_protection": row["fire_protection_provider_code"],
                "census_tract_fips": row["census_tract_fips_code"],
                "census_block_fips": row["census_block_fips_code"],
            },
            "area": {
                "legal_acres": float(row["legal_area_acres"]) if row["legal_area_acres"] is not None else None,
                "estimated_acres": float(row["estimated_area_acres"]) if row["estimated_area_acres"] is not None else None,
            },
            "map_number": row["taxmap_number"],
        },
        "deep_links": {
            "lane_at_search": at_search_url,
            "rlid_property_report": rlid_link,
            "lane_at_home": "https://www.lanecountyor.gov/cms/one.aspx?pageId=3252952",
        },
        "in_flight": [
            "Multi-year tax payment history + bill status — apps.lanecounty.org A&T detail is JS-rendered; account-detail URL contract not captured. Snapshot above carries the current roll year only.",
            "RLID full property report — subscription-gated. Deep link above lands the user on the property summary regardless; full report requires LCOG / paid public account.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO lane_assessment_cache (taxlot, account_number, payload_json, fetched_at, last_error)
                   VALUES (%s, %s, %s::jsonb, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       account_number = EXCLUDED.account_number,
                       payload_json = EXCLUDED.payload_json,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, row["reference_account_number"], _json.dumps(payload)),
            )
            cn.commit()
    except Exception as e:
        print(f"[lane_assessment] cache write error: {e}")

    return payload


def fetch_lane_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Lane recorded-documents / deed chain / sales history for a taxlot.

    CURRENTLY IN FLIGHT — Lane County Clerk has no public queryable API; RLID
    consolidates but full reports are subscription-gated. Returns current owner
    + canonical RLID deep link with honest IN FLIGHT framing.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, owner_name, documents_json, document_count, fetched_at "
                    "FROM lane_records_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_LANE_CACHE_TTL_HOURS):
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "owner_name": cached["owner_name"],
                            "snapshot": {"owner_now": cached["owner_name"]},
                            "documents": cached["documents_json"] or [],
                            "document_count": cached["document_count"] or 0,
                            "deep_links": {
                                "clerk_recordings": "https://www.lanecountyor.gov/cms/one.aspx?pageId=2818275",
                            },
                            "in_flight": [
                                "Full chain of title / deed history — Lane Clerk has no public API. RLID subscription tier or paid Tyler integration are the open paths.",
                            ],
                            "fetched_at": cached["fetched_at"].isoformat(),
                        }
        except Exception as e:
            print(f"[lane_records] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, reference_owner_name, owner_count,
                          reference_full_address, reference_plat_name,
                          rlid_link
                   FROM parcels_lane WHERE taxlot = %s LIMIT 1""",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_lane", "taxlot": taxlot}

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "owner_name": row["reference_owner_name"],
        "snapshot": {
            "owner_now": row["reference_owner_name"],
            "owner_count": row["owner_count"],
            "situs_address": row["reference_full_address"],
            "plat": row["reference_plat_name"],
        },
        "documents": [],
        "document_count": 0,
        "deep_links": {
            "rlid_property_report": row["rlid_link"],
            "clerk_recordings": "https://www.lanecountyor.gov/cms/one.aspx?pageId=2818275",
            "lane_at_search": f"{_LANE_AT_BASE}/?searchType=MapTaxlot&maptaxlot={taxlot}",
        },
        "in_flight": [
            "Full chain of title / deed history — Lane Clerk has no public queryable API as of 2026-05-27. Open paths: (a) RLID paid tier, (b) paid Tyler integration, (c) per-deed Clerk export.",
            "Sales chain — same blocker. Most-recent ownership above is current as of last L1 refresh.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO lane_records_cache (taxlot, owner_name, documents_json, document_count, fetched_at, last_error)
                   VALUES (%s, %s, '[]'::jsonb, 0, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       owner_name = EXCLUDED.owner_name,
                       documents_json = EXCLUDED.documents_json,
                       document_count = EXCLUDED.document_count,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, row["reference_owner_name"]),
            )
            cn.commit()
    except Exception as e:
        print(f"[lane_records] cache write error: {e}")

    return payload


def fetch_lane_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Lane permits — branches by planning_jurisdiction_code.

    Eugene (juris='EUG') → Eugene PDD portal (separate from state Accela; IN FLIGHT).
    Springfield + smaller cities + unincorporated → Oregon state Accela via
    fetch_county_permits with county_fips='039'.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT planning_jurisdiction_code, incorporated_city_code,
                          reference_full_address
                   FROM parcels_lane WHERE taxlot = %s LIMIT 1""",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_lane", "taxlot": taxlot}

    juris = (row["planning_jurisdiction_code"] or "").upper()
    incorp = (row["incorporated_city_code"] or "").upper()

    if juris == "EUG" or incorp == "EUG":
        return {
            "found": True,
            "from_cache": False,
            "taxlot": taxlot,
            "county_fips": "039",
            "routed_via": "eugene_pdd",
            "jurisdiction": "City of Eugene (PDD — separate from state Accela)",
            "status": "deep_link_only",
            "permit_count": 0,
            "permits": [],
            "deep_links": {
                "eugene_pdd": _EUGENE_PDD_PERMITS,
                "eugene_pdd_home": "https://www.eugene-or.gov/3360/Permits",
            },
            "in_flight": [
                "Structured scrape of Eugene PDD permit portal — Eugene runs its own EnerGov-style permitting separate from the state Oregon Accela. Portal endpoint contract not captured this pass.",
                "Until then: clicking the deep link lands the user on Eugene PDD's permit portal.",
            ],
            "fetched_at": _dt.now(_tz.utc).isoformat(),
        }

    result = fetch_county_permits(taxlot, "039", force_refresh)
    if isinstance(result, dict):
        result["routed_via"] = "oregon_state_accela"
        if juris == "SPR" or incorp == "SPR":
            result["jurisdiction"] = "City of Springfield (Oregon state Accela)"
        else:
            result["jurisdiction"] = f"Lane County unincorporated / small city (Oregon state Accela) — juris={juris or 'LAN'}"
    return result


# =============================================================================
# CLACKAMAS Layer 2 + Layer 3 Fetchers (s3 Yindo session — 2026-05-27, Portland metro trifecta close)
# =============================================================================
# Reality check (2026-05-27, s3 Yindo session — Clackamas after Washington):
# Clackamas's hosted Taxlots view is THE OPPOSITE of Multnomah's bulk service —
# it ships ONLY TLNO + PARCEL_NUMBER + SITUS + SITUS_CITY + SITUS_ZIP + TAXCODE
# + geometry, with owner/valuation/sale/zoning explicitly stripped from the
# public view by source (per the HFLV description). Rich data lives behind the
# A&T portal at ascendweb.clackamas.us — ASP.NET WebForms with __VIEWSTATE
# gating (confirmed via probe; same blocker that lost s1's Accela scrape, s2's
# MultcoPropTax scrape, and s3's earlier washcotax scrape).
#
# What works: (a) Layer 1 inline data (taxlot + parcel_number + situs +
# situs_city + tax_code) is the source of truth for the address/account
# snapshot. (b) Verified deep link to ascendweb (account-number search landing
# is captcha-free; the POST to detail is gated). Owner / valuation / sale /
# zoning / tax history flagged IN FLIGHT pending viewstate capture.
#
# Records: Clackamas County Clerk Recording Division has no public search
# portal located (recording.clackamas.us 404s; the recording page lists
# e-Recording submitters but no read surface). Best-effort IN FLIGHT with
# deep link to the recording division homepage — same posture as Washington.
#
# Permits: Clackamas runs its OWN Accela ACA tenant at aca-prod.accela.com/
# clackamas (NOT the state oregon.gov Accela aca-oregon.accela.com used by
# Crook/Marion/Jefferson/Lane via fetch_county_permits; AND NOT Washington's
# permits.washingtoncountyor.gov county-direct instance). So
# fetch_clackamas_permits is its OWN function — third distinct Accela tenant
# we cache. Recon also surfaced "Development Direct" (Avolve cloud) at
# clackamas-or-us.avolvecloud.com which is the submission surface for new
# permits; aca-prod is the search/lookup surface.
#
# IMPORTANT DATA POINT FOR THE BRIEF'S REUSE HYPOTHESIS: the brief flagged
# Clackamas as "high-probability for direct reuse" of fetch_county_permits.
# Recon disproves that — Clackamas, like Washington (and likely Multnomah,
# which uses Portland Maps), runs its own Accela tenant. The pattern appears
# to be: Tier 1 Flagship counties run county-direct Accela; smaller counties
# (Crook, Jefferson) use the state ePermitting portal. Carries forward to KB_82.
# =============================================================================

_CLACK_CACHE_TTL_HOURS = 24
_CLACK_AT_BASE = "http://ascendweb.clackamas.us"
_CLACK_AT_ACCOUNT_URL = _CLACK_AT_BASE + "/default.aspx"  # GET with mAccount=... pre-fills field
_CLACK_PERMITS_BASE = "https://aca-prod.accela.com/clackamas"
_CLACK_PERMITS_SEARCH = f"{_CLACK_PERMITS_BASE}/Cap/CapHome.aspx?module=Building"
_CLACK_DEV_DIRECT_HOMEPAGE = "https://clackamas-or-us.avolvecloud.com/Portal/Login/Index/Clackamas-County-OR"
_CLACK_RECORDING_HOMEPAGE = "https://www.clackamas.us/recording"


def fetch_clack_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Clackamas County assessment + property snapshot for a taxlot.

    Built on parcels_clackamas Layer 1 inline data (taxlot, parcel_number,
    situs, situs_city, situs_zip, tax_code) + geometry-derived acreage
    (geodesic via ST_Area(::geography); Web Mercator shape_area at source
    is latitude-distorted). Owner, valuation snapshot, multi-year certified
    values, sale chain, deed instruments, and tax payment history are IN
    FLIGHT pending viewstate capture against ascendweb.clackamas.us.

    Returns: {found, taxlot, parcel_number, snapshot{size,address,tax_code},
              deep_links{at_account_search,at_homepage}, in_flight[],
              fetched_at, from_cache}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, parcel_number, payload_json, fetched_at, last_error "
                    "FROM clack_assessment_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_CLACK_CACHE_TTL_HOURS) and cached["payload_json"]:
                        payload = cached["payload_json"]
                        payload["from_cache"] = True
                        payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                        return payload
        except Exception as e:
            print(f"[clack_assessment] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT taxlot, parcel_number, tax_code,
                          situs_address, situs_city, situs_zip,
                          ROUND((ST_Area(geom::geography) / 4046.8564224)::numeric, 3) AS acres,
                          ST_AsText(ST_Centroid(geom)) AS centroid
                   FROM parcels_clackamas WHERE taxlot = %s LIMIT 1""",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_clackamas", "taxlot": taxlot}

    parcel_number = row["parcel_number"]
    at_account_url = f"{_CLACK_AT_ACCOUNT_URL}?mAccount={parcel_number}" if parcel_number else _CLACK_AT_ACCOUNT_URL

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "parcel_number": parcel_number,
        "snapshot": {
            "size": {
                "acres": float(row["acres"]) if row["acres"] is not None else None,
            },
            "address": {
                "situs_address": row["situs_address"],
                "situs_city": row["situs_city"],
                "situs_zip": row["situs_zip"],
            },
            "tax_code_area": row["tax_code"],
            "location": {
                "centroid_wkt": row["centroid"],
            },
        },
        "deep_links": {
            "at_account_search": at_account_url,
            "at_homepage": _CLACK_AT_BASE + "/",
        },
        "in_flight": [
            "owner_of_record + mailing_address (ascendweb.clackamas.us is ASP.NET WebForms with __VIEWSTATE-gated PARCEL_NUMBER → property-detail resolution).",
            "valuation snapshot (land/improvement/M50 assessed) + multi-year certified values (same source).",
            "sales history + deed instruments (same source — also a 'Records' surface to confirm via fetch_clack_records).",
            "zoning code + label (Clackamas's public view strips zoning; cmap.clackamas.us is the JS-driven viewer — separate fetch needed).",
            "tax payment history + current bill status (same source).",
        ],
        "source": "parcels_clackamas Layer 1 + ascendweb A&T deep link",
        "source_url": at_account_url,
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO clack_assessment_cache (taxlot, parcel_number, payload_json, fetched_at, last_error)
                   VALUES (%s, %s, %s::jsonb, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       parcel_number = EXCLUDED.parcel_number,
                       payload_json = EXCLUDED.payload_json,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, parcel_number, _json.dumps(payload, default=str)),
            )
            cn.commit()
    except Exception as e:
        print(f"[clack_assessment] cache write error: {e}")

    return payload


def fetch_clack_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Clackamas County recorded documents (deeds, mortgages, liens).

    IN FLIGHT — Clackamas County Clerk Recording Division has no public
    search portal located (recording.clackamas.us 404s; the division page at
    clackamas.us/recording lists only e-Recording submitter contacts, no read
    surface). Deed/sale data is NOT inline in parcels_clackamas (the public
    view strips ownership info). Best path forward: viewstate-captured
    ascendweb query, or paid-tier subscription, or Simplifile/CSC/ePN
    integration. Same blocker class as fetch_wash_records / fetch_multco_records.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "SELECT taxlot, parcel_number FROM parcels_clackamas WHERE taxlot = %s LIMIT 1",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_clackamas", "taxlot": taxlot}

    parcel_number = row["parcel_number"]
    at_account_url = f"{_CLACK_AT_ACCOUNT_URL}?mAccount={parcel_number}" if parcel_number else _CLACK_AT_ACCOUNT_URL

    return {
        "found": True,
        "in_flight": True,
        "taxlot": row["taxlot"],
        "parcel_number": parcel_number,
        "best_available_from_layer_1": {
            "note": "Clackamas's hosted Taxlots view explicitly strips owner/sale/deed data (per source description). Use fetch_clack_assessment for size+address snapshot; deed chain is fully IN FLIGHT.",
        },
        "in_flight_reason": (
            "Clackamas County Clerk Recording Division has no public search "
            "portal located. recording.clackamas.us 404s; the division page "
            "lists only e-Recording submitter contacts (Simplifile / CSC / ePN), "
            "no read surface. Best path forward: viewstate-captured ascendweb "
            "query, paid-tier subscription, or e-Recording vendor integration. "
            "Pending Iosif decision — same blocker class as fetch_wash_records "
            "and fetch_multco_records."
        ),
        "deep_links": {
            "at_account_search": at_account_url,
            "recording_division_homepage": _CLACK_RECORDING_HOMEPAGE,
        },
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def fetch_clackamas_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Building permits for a Clackamas County parcel.

    Clackamas runs its OWN Accela ACA tenant at aca-prod.accela.com/clackamas
    (DISTINCT from the state oregon.gov Accela used by Crook/Marion/Jefferson/
    Lane via fetch_county_permits, AND DISTINCT from Washington's
    permits.washingtoncountyor.gov county-direct instance). New permit
    submissions live on a separate Avolve cloud surface (Development Direct).

    Same deep-link-only IN FLIGHT pattern as fetch_county_permits and
    fetch_washington_permits: a verified Accela search URL is returned,
    scrape upgrade gated on viewstate-capture or Playwright. Cache table is
    sized for the eventual real payload so the upgrade drops in without
    schema change.

    Jurisdiction routing: aca-prod.accela.com/clackamas covers Clackamas
    County (unincorporated) + many of its cities. Oregon City / Lake Oswego /
    West Linn / Happy Valley / Milwaukie / Wilsonville may run their own
    portals; v1 surfaces the county Accela deep link and notes per-city
    routing as IN FLIGHT.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, jurisdiction, permits_json, permit_count, aca_search_url, "
                    "fetched_at, last_error FROM clack_permits_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_CLACK_CACHE_TTL_HOURS):
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "jurisdiction": cached["jurisdiction"],
                            "status": "deep_link_only",
                            "permit_count": cached["permit_count"],
                            "permits": cached["permits_json"] or [],
                            "deep_links": {
                                "accela_search": cached["aca_search_url"],
                                "development_direct": _CLACK_DEV_DIRECT_HOMEPAGE,
                            },
                            "in_flight": [
                                "Server-side scrape of the county Accela result page — gated on viewstate capture or Playwright.",
                                "Per-city portal routing (Oregon City / Lake Oswego / West Linn / Happy Valley / Milwaukie / Wilsonville) for parcels inside city limits.",
                            ],
                            "fetched_at": cached["fetched_at"].isoformat(),
                        }
        except Exception as e:
            print(f"[clack_permits] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                "SELECT taxlot, situs_city FROM parcels_clackamas WHERE taxlot = %s LIMIT 1",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_clackamas", "taxlot": taxlot}

    situs_city = (row["situs_city"] or "").strip().upper()
    jurisdiction = "Clackamas County (county-direct Accela)"
    if situs_city == "OREGON CITY":
        jurisdiction = "Oregon City (city portal likely separate; county Accela may also list)"
    elif situs_city == "LAKE OSWEGO":
        jurisdiction = "Lake Oswego (city portal likely separate)"
    elif situs_city == "WEST LINN":
        jurisdiction = "West Linn (city portal likely separate)"
    elif situs_city == "HAPPY VALLEY":
        jurisdiction = "Happy Valley (city portal likely separate)"
    elif situs_city == "MILWAUKIE":
        jurisdiction = "Milwaukie (city portal likely separate)"
    elif situs_city == "WILSONVILLE":
        jurisdiction = "Wilsonville (city portal likely separate)"

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "jurisdiction": jurisdiction,
        "situs_city": row["situs_city"],
        "status": "deep_link_only",
        "permit_count": 0,
        "permits": [],
        "deep_links": {
            "accela_search": _CLACK_PERMITS_SEARCH,
            "development_direct": _CLACK_DEV_DIRECT_HOMEPAGE,
        },
        "in_flight": [
            "Server-side scrape of the county Accela result page — gated on viewstate capture or Playwright (same blocker as fetch_county_permits for the state portal and fetch_washington_permits for Washington's county Accela).",
            "Per-city portal routing — Oregon City / Lake Oswego / West Linn / Happy Valley / Milwaukie / Wilsonville may run separate city permit systems; v1 only covers the county Accela.",
            "Development Direct (Avolve cloud) is the submission surface for NEW permits and not a search surface — the Accela link above is the right deep link for permit history.",
            "Until the scrape lands: clicking the deep link drops the user on the county's Accela search where pasting the taxlot or address returns the live list in one search submit.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO clack_permits_cache (taxlot, jurisdiction, permits_json, permit_count, aca_search_url, fetched_at, last_error)
                   VALUES (%s, %s, '[]'::jsonb, 0, %s, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       jurisdiction = EXCLUDED.jurisdiction,
                       aca_search_url = EXCLUDED.aca_search_url,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, jurisdiction, _CLACK_PERMITS_SEARCH),
            )
            cn.commit()
    except Exception as e:
        print(f"[clack_permits] cache write error: {e}")

    return payload


# =============================================================================
# JACKSON Layer 2 + Layer 3 Fetchers (s4 Yindo session — 2026-05-27, FIRST TIER 2 MAJOR)
# =============================================================================
# Reality check (2026-05-27, s4 Yindo session — first Tier 2 county shipped):
# Jackson is the cleanest data ecosystem of any Oregon county TERRA has touched.
# Three loads, three honest reads:
#
#   1. Layer 1 inline is Marion-rich plus more — FEEOWNER, SITEADD, full mailing
#      address, ACCOUNT, real-market values (LANDVALUE / IMPVALUE), assessed
#      values (ASSESSLAND / ASSESSIMP), acreage, prop_class, year built, tax
#      code, commercial sqft, building code, lot dimensions. Zoning is NOT
#      inline (separate ZoningDistricts FeatureServer on the same on-prem
#      server, ingested into jackson_zoning as L1 supplementary). So
#      fetch_jackson_assessment is a SQL pass-through over L1 inline +
#      jackson_zoning spatial join — NO scraper, NO viewstate gating, NO HTTP.
#
#   2. Layer 2 records — Jackson Clerk recordings live at
#      apps.jacksoncountyor.gov/DigitalResearchRoomPublic (OnBase Public
#      Access). Search-only API, viewstate-gated retrieval. Same IN FLIGHT
#      pattern as Marion / Washington / Lane — surface the most-recent
#      fee-owner from L1 inline plus a verified deep link.
#
#   3. Layer 3 permits — JACKSON IS SPECIAL. Jackson publishes 243k building
#      permits + 40k land-use permits as a public point FeatureServer at
#      spatial.jacksoncountyor.gov DevServ/Permit, with PRE-BUILT ACA-Oregon
#      Accela CapDetail deep links on every record. fetch_jackson_permits
#      runs a live spatial query (parcel envelope intersects permit point)
#      and returns REAL STRUCTURED PERMITS with deep links — NOT the
#      deep-link-only fallback we use for Crook / Marion (where the Accela
#      scrape is viewstate-blocked). This is the cleanest permit Layer 3
#      shipped to date and the blueprint for any future county that exposes
#      permits via GIS REST.
# =============================================================================

_JACKSON_PERMITS_CACHE_TTL_HOURS = 24
_JACKSON_PDO_BASE = "https://pdo.jacksoncountyor.gov/pdo/index.cfm"
_JACKSON_CLERK_RECORDS_PAGE = "https://apps.jacksoncountyor.gov/DigitalResearchRoomPublic"
_JACKSON_RECORDING_INFO_PAGE = (
    "https://jacksoncountyor.gov/departments/clerk/recording/"
    "services/recorded_document_research_and_copies.php"
)
_JACKSON_TAXMAP_BASE = (
    "https://spatial.jacksoncountyor.gov/arcgis/rest/services/"
    "Assessment/MapIndex/MapServer"
)
_JACKSON_PERMITS_BLDG_URL = (
    "https://spatial.jacksoncountyor.gov/arcgis/rest/services/"
    "DevServ/Permit/FeatureServer/0/query"
)
_JACKSON_PERMITS_LANDUSE_URL = (
    "https://spatial.jacksoncountyor.gov/arcgis/rest/services/"
    "DevServ/Permit/FeatureServer/1/query"
)
_JACKSON_USER_AGENT = "SKOpi-TERRA/1.0 (+https://skopi.io)"


def _jackson_l1_row(taxlot: str):
    """Fetch the L1 row for a Jackson taxlot + spatially-joined zoning + bbox.

    Returns a dict with all inline assessor fields, the spatially-joined zone
    code/desc/comp-plan-des, the parcel centroid (text WKT), and the parcel
    envelope coordinates so callers don't need a second query.
    """
    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT p.taxlot, p.county_fips, p.account, p.lottype,
                       p.map_number, p.map_num, p.tm_maplot,
                       p.owner_name, p.contract_buyer, p.in_care_of,
                       p.mailing_address_1, p.mailing_address_2,
                       p.mailing_city, p.mailing_state, p.mailing_zip,
                       p.situs_address, p.address_num, p.street_name,
                       p.prop_class, p.build_code, p.year_built, p.comm_sqft,
                       p.lot_depth, p.lot_width,
                       p.acreage, p.gis_area,
                       p.land_value, p.imp_value,
                       p.assess_land, p.assess_imp,
                       p.tax_code,
                       ST_AsText(ST_Centroid(p.geom)) AS parcel_centroid,
                       ST_XMin(p.geom) AS xmin, ST_YMin(p.geom) AS ymin,
                       ST_XMax(p.geom) AS xmax, ST_YMax(p.geom) AS ymax,
                       z.zone_code, z.zone_desc, z.comp_plan_des,
                       p.ingested_at
                FROM parcels_jackson p
                LEFT JOIN LATERAL (
                    SELECT z.zone_code, z.zone_desc, z.comp_plan_des
                    FROM jackson_zoning z
                    WHERE ST_Intersects(z.geom, ST_Centroid(p.geom))
                    ORDER BY ST_Area(ST_Intersection(z.geom, p.geom)) DESC NULLS LAST
                    LIMIT 1
                ) z ON true
                WHERE p.taxlot = %s
                LIMIT 1
                """,
                (taxlot,),
            )
            return cur.fetchone()
    except Exception as e:
        print(f"[jackson_l1_row] DB error: {e}")
        return None


def fetch_jackson_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Jackson assessment + ownership + zoning snapshot for a taxlot.

    Pass-through over parcels_jackson L1 inline + jackson_zoning spatial join
    (no external HTTP). Returns owner / situs / mailing / account / acreage /
    current real-market + assessed valuation / building metrics / zoning code +
    description + comp-plan designation, plus verified deep links to the
    Jackson Property Data Online portal and the Clerk's Digital Research Room.

    IN FLIGHT — flagged honestly, never invented:
      - Multi-year tax payment history → PDO deep-link parameterization is not
        yet confirmed; the broker clicks through and pastes the account.
      - Full deed chain → Digital Research Room is OnBase Public Access with
        viewstate retrieval; surface most-recent owner from L1 + deep link.

    Returns: {found, taxlot, county_fips, snapshot{...}, deep_links{...},
              in_flight[], fetched_at, from_cache, source}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _jackson_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_jackson", "taxlot": taxlot}

    rmv_land = row["land_value"]
    rmv_imp = row["imp_value"]
    rmv_total = (rmv_land or 0) + (rmv_imp or 0) if (rmv_land is not None or rmv_imp is not None) else None
    assess_land = row["assess_land"]
    assess_imp = row["assess_imp"]
    assess_total = (assess_land or 0) + (assess_imp or 0) if (assess_land is not None or assess_imp is not None) else None

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_jackson L1 inline + jackson_zoning spatial join + Jackson PDO/Clerk deep links",
        "snapshot": {
            "owner": {
                "primary": row["owner_name"],
                "contract_buyer": row["contract_buyer"],
                "in_care_of": row["in_care_of"],
                "mailing_address_1": row["mailing_address_1"],
                "mailing_address_2": row["mailing_address_2"],
                "mailing_city": row["mailing_city"],
                "mailing_state": row["mailing_state"],
                "mailing_zip": row["mailing_zip"],
            },
            "situs": {
                "address": row["situs_address"],
                "address_num": row["address_num"],
                "street_name": row["street_name"],
            },
            "account": {
                "number": row["account"],
                "lottype": row["lottype"],
                "prop_class": row["prop_class"],
                "tax_code": row["tax_code"],
                "map_number": row["map_number"],
                "map_num": row["map_num"],
                "tm_maplot": row["tm_maplot"],
            },
            "acreage": {
                "assessor_of_record": float(row["acreage"]) if row["acreage"] is not None else None,
                "gis_sqft": float(row["gis_area"]) if row["gis_area"] is not None else None,
            },
            "zoning": {
                "code": row["zone_code"],
                "label": row["zone_desc"],
                "comp_plan_designation": row["comp_plan_des"],
            },
            "valuation": {
                "rmv_land": rmv_land,
                "rmv_imp": rmv_imp,
                "rmv_total": rmv_total,
                "assessed_land": assess_land,
                "assessed_imp": assess_imp,
                "assessed_total": assess_total,
            },
            "building": {
                "build_code": row["build_code"],
                "year_built": row["year_built"],
                "commercial_sqft": row["comm_sqft"],
                "lot_depth": row["lot_depth"],
                "lot_width": row["lot_width"],
            },
        },
        "deep_links": {
            "property_data_online": _JACKSON_PDO_BASE,
            "clerk_records_room": _JACKSON_CLERK_RECORDS_PAGE,
            "clerk_info_page": _JACKSON_RECORDING_INFO_PAGE,
            "tax_map_service": _JACKSON_TAXMAP_BASE,
        },
        "in_flight": [
            "Property Data Online (PDO) deep-link parameterization — landing page accepts account / owner / address search; the ?Account= query string is not yet confirmed, so the broker clicks through and pastes ACCOUNT to land on the parcel detail.",
            "Multi-year tax payment history — same PDO viewstate-gated retrieval pattern as Marion / Crook; coming once we wire viewstate capture or paid tier.",
            "Full chain-of-title — Digital Research Room (OnBase Public Access) deep link surfaced for the broker; structured scrape IN FLIGHT.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def fetch_jackson_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Jackson Clerk recorded-document snapshot.

    Returns the most-recent fee-owner from parcels_jackson L1 (Jackson's bulk
    taxlot service ships the current fee-owner inline) plus deep links to the
    Digital Research Room and the County Clerk recording page. The
    multi-instrument history is IN FLIGHT because the Digital Research Room is
    OnBase Public Access with viewstate-gated retrieval — same scrape blocker
    we hit on Marion's MCASR PropertySummary and Washington's washcotax. Honest
    framing per DOCTRINE-HONEST-IN-FLIGHT-01.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    row = _jackson_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_jackson", "taxlot": taxlot}

    return {
        "found": True,
        "from_cache": False,
        "taxlot": row["taxlot"],
        "county_fips": row["county_fips"],
        "source": "parcels_jackson L1 inline owner-of-record + Jackson Clerk deep links",
        "owner_of_record": {
            "primary": row["owner_name"],
            "contract_buyer": row["contract_buyer"],
            "in_care_of": row["in_care_of"],
            "mailing_address_1": row["mailing_address_1"],
            "mailing_city": row["mailing_city"],
            "mailing_state": row["mailing_state"],
            "mailing_zip": row["mailing_zip"],
        },
        "deep_links": {
            "digital_research_room": _JACKSON_CLERK_RECORDS_PAGE,
            "clerk_info_page": _JACKSON_RECORDING_INFO_PAGE,
            "property_data_online": _JACKSON_PDO_BASE,
        },
        "in_flight": [
            "Full multi-instrument chain of title — Digital Research Room is OnBase Public Access with viewstate-gated document retrieval; deep link lets the broker pull the full chain themselves until structured scrape lands.",
            "Recorded sale price + instrument number history — same blocker as the deed chain.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }


def _jackson_query_permits(layer_url: str, xmin: float, ymin: float, xmax: float, ymax: float) -> list[dict]:
    """Hit the Jackson DevServ/Permit FeatureServer with a parcel-envelope spatial filter.

    Returns a list of permit dicts (raw attributes plus convenience fields).
    Empty list on miss, raises on transport failure (caller catches).

    Why envelope rather than full polygon? Permit POINTS that touch a parcel's
    bounding box are 1:1 mapped to that parcel's locator the county uses
    upstream — the small risk of capturing an off-parcel point at the bbox
    edges is acceptable in v1 (we surface the FULLADDR and LOCDESC so the
    broker can sanity-check), and the envelope query is dramatically faster
    than the full polygon ST_Intersects path on a 243k-row layer.
    """
    params = {
        "where": "1=1",
        "outFields": "*",
        "geometry": f"{xmin},{ymin},{xmax},{ymax}",
        "geometryType": "esriGeometryEnvelope",
        "inSR": "4326",
        "spatialRel": "esriSpatialRelIntersects",
        "f": "json",
        "resultRecordCount": "200",
    }
    # Jackson's ArcGIS Server returns a "Content-Security-Policy : ..." header
    # with a SPACE before the colon — violates RFC 7230, httpx refuses to parse
    # the response. urllib is lenient enough to accept it. Same pattern is used
    # by the parcel ingest script (`urllib.request.urlopen`) — keep consistent.
    from urllib.parse import urlencode as _urlencode
    from urllib.request import Request as _UrlReq, urlopen as _urlopen
    url = layer_url + "?" + _urlencode(params)
    try:
        req = _UrlReq(url, headers={"User-Agent": _JACKSON_USER_AGENT, "Accept": "application/json"})
        with _urlopen(req, timeout=20.0) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        raise RuntimeError(f"Jackson permits fetch failed: {e}") from e

    features = payload.get("features", []) or []
    out = []
    for f in features:
        attrs = f.get("attributes", {}) or {}
        for k in ("SUBMITDT", "APPROVEDT"):
            v = attrs.get(k)
            if isinstance(v, (int, float)) and v:
                try:
                    attrs[k + "_iso"] = _dt.fromtimestamp(v / 1000, tz=_tz.utc).date().isoformat()
                except Exception:
                    pass
        out.append(attrs)
    return out


def fetch_jackson_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Live permits for a Jackson parcel via the public GIS Permit FeatureServer.

    NOT a fetch_county_permits wrapper. Jackson publishes 243k building permits
    + 40k land-use permits as a point FeatureServer at spatial.jacksoncountyor.gov
    with PRE-BUILT ACA-Oregon Accela CapDetail deep links on every record, so we
    can return REAL structured permit data — NOT the deep-link-only fallback
    that fetch_county_permits returns for counties where Accela is viewstate-
    gated.

    Strategy: pull the parcel envelope from parcels_jackson; spatial-intersect
    against /DevServ/Permit/FeatureServer/0 (Building) and /1 (Land Use); cache
    the joined result 24h in jackson_permits_cache.

    Returns: {found, taxlot, county_fips, permit_count, bldg_count, landuse_count,
              permits[], jurisdictions[], deep_links{aca_search}, fetched_at,
              from_cache, in_flight[]}
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT permits_json, permit_count, bldg_count, landuse_count, "
                    "       query_bbox, fetched_at, last_error "
                    "FROM jackson_permits_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_JACKSON_PERMITS_CACHE_TTL_HOURS):
                        permits = cached["permits_json"] or []
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "county_fips": "029",
                            "permit_count": cached["permit_count"],
                            "bldg_count": cached["bldg_count"],
                            "landuse_count": cached["landuse_count"],
                            "permits": permits,
                            "jurisdictions": sorted({p.get("JURISDICTION") for p in permits if p.get("JURISDICTION")}),
                            "deep_links": {
                                "aca_jackson_search": "https://aca-oregon.accela.com/oregon/Cap/CapHome.aspx?module=Building&AgencyCode=JACKSON_CO",
                            },
                            "fetched_at": cached["fetched_at"].isoformat(),
                            "in_flight": [],
                        }
        except Exception as e:
            print(f"[jackson_permits] cache read error: {e}")

    row = _jackson_l1_row(taxlot)
    if not row:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_jackson", "taxlot": taxlot}

    xmin, ymin = float(row["xmin"]), float(row["ymin"])
    xmax, ymax = float(row["xmax"]), float(row["ymax"])
    bbox_str = f"{xmin:.6f},{ymin:.6f},{xmax:.6f},{ymax:.6f}"

    try:
        bldg = _jackson_query_permits(_JACKSON_PERMITS_BLDG_URL, xmin, ymin, xmax, ymax)
        landuse = _jackson_query_permits(_JACKSON_PERMITS_LANDUSE_URL, xmin, ymin, xmax, ymax)
    except Exception as e:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    """INSERT INTO jackson_permits_cache
                          (taxlot, permits_json, permit_count, bldg_count, landuse_count,
                           query_bbox, fetched_at, last_error)
                       VALUES (%s, '[]'::jsonb, 0, 0, 0, %s, NOW(), %s)
                       ON CONFLICT (taxlot) DO UPDATE SET
                          query_bbox = EXCLUDED.query_bbox,
                          fetched_at = NOW(),
                          last_error = EXCLUDED.last_error""",
                    (taxlot, bbox_str, str(e)),
                )
                cn.commit()
        except Exception:
            pass
        return {"found": False, "taxlot": taxlot, "reason": f"Jackson permits fetch error: {e}"}

    for p in bldg:
        p["_layer"] = "Building"
    for p in landuse:
        p["_layer"] = "Land Use"
    permits = bldg + landuse
    permits.sort(key=lambda p: p.get("SUBMITDT") or 0, reverse=True)

    jurisdictions = sorted({p.get("JURISDICTION") for p in permits if p.get("JURISDICTION")})

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "county_fips": "029",
        "permit_count": len(permits),
        "bldg_count": len(bldg),
        "landuse_count": len(landuse),
        "permits": permits,
        "jurisdictions": jurisdictions,
        "deep_links": {
            "aca_jackson_search": "https://aca-oregon.accela.com/oregon/Cap/CapHome.aspx?module=Building&AgencyCode=JACKSON_CO",
        },
        "fetched_at": _dt.now(_tz.utc).isoformat(),
        "in_flight": [],
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO jackson_permits_cache
                      (taxlot, permits_json, permit_count, bldg_count, landuse_count,
                       query_bbox, fetched_at, last_error)
                   VALUES (%s, %s::jsonb, %s, %s, %s, %s, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                      permits_json = EXCLUDED.permits_json,
                      permit_count = EXCLUDED.permit_count,
                      bldg_count = EXCLUDED.bldg_count,
                      landuse_count = EXCLUDED.landuse_count,
                      query_bbox = EXCLUDED.query_bbox,
                      fetched_at = NOW(),
                      last_error = NULL""",
                (taxlot, _json.dumps(permits), len(permits), len(bldg), len(landuse), bbox_str),
            )
            cn.commit()
    except Exception as e:
        print(f"[jackson_permits] cache write error: {e}")

    return payload


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


# =============================================================================
# LINN County L2/L3 — re-exported from linn_terra (separate module to dodge the
# multi-parallel-session edit thrash on this file; functional contract identical
# to every other county). End-of-file so `from .orb_db import fetch_linn_*`
# keeps working.
# =============================================================================
from .linn_terra import (
    fetch_linn_assessment,
    fetch_linn_records,
    fetch_linn_permits,
)


# =============================================================================
# BENTON Layer 2 + Layer 3 Fetchers (s1 Yindo session — 2026-05-27, post-Marion)
# =============================================================================
# Reality check (2026-05-27, s1 Yindo session — Benton after Marion ship):
# Benton's TaxlotOwners FeatureServer ships situs + owner + mailing inline,
# but is OWNER-EXPLODED — same parcel polygon repeats 1..N times with one row
# per Party_Name. 107,988 raw rows compress to 35,365 unique parcels (~2.86
# rows/parcel avg; trust co-trustees and multi-owner deeds are the main drivers).
# Owner aggregation happens at query time via STRING_AGG(Party_Name) GROUP BY
# ORTaxlot in the COUNTY_REGISTRY normalized lookup (orb_db.lookup_parcel_by_point).
#
# What works: (a) L1 inline data is the source of truth for owner / situs /
# mailing / map_number / account / tax_code_area / ORTaxlot. (b) Verified deep
# link to the Property Account Search landing page (assessment.bentoncountyor.gov).
# The portal's bcaps search form is POST-only with no GET parameter surface —
# per-parcel deep linking is IN FLIGHT pending bcaps form-params reverse-engineering.
# (c) Zoning / valuation / sale history NOT in L1 — flagged IN FLIGHT for later
# Benton ZoningService FeatureServer join (gis.co.benton.or.us/.../ZoningService).
#
# Records: Benton County Records & Elections at re.bentoncountyor.gov runs a
# public recorded-document index search at records.co.benton.or.us/Recording/.
# Structured search + scrape IN FLIGHT (same Tapestry/Laredo-style portal pattern
# as Lane/Washington/Clackamas — viewstate or session-token gating likely).
# Best-effort deep link surfaced now.
#
# Permits: Benton participates in the STATE Oregon ePermitting portal at
# aca-oregon.accela.com/oregon — confirmed via cd.bentoncountyor.gov/electronic-
# permitting which links directly to that surface. So fetch_benton_permits is a
# CLEAN ONE-LINE WRAPPER around fetch_county_permits('003'). FIPS 003 added to
# _OREGON_EPERMITTING_FIPS. Validates the brief's reuse hypothesis for Benton.
# =============================================================================

_BENTON_CACHE_TTL_HOURS = 24
_BENTON_ASSESS_BASE = "https://assessment.bentoncountyor.gov"
_BENTON_RECORDS_PORTAL = "https://records.co.benton.or.us/Recording/"
_BENTON_RECORDS_INDEX_LANDING = "https://re.bentoncountyor.gov/real-property-records-index-search/"


def fetch_benton_assessment(taxlot: str, force_refresh: bool = False) -> dict:
    """Benton assessment snapshot for a taxlot — SQL pass-through over parcels_benton.

    Returns aggregated owner (STRING_AGG over Party_Name) + situs + mailing +
    portal deep link, with 24h cache. Valuation / zoning / sale history are
    NOT in Benton's L1; those are flagged IN FLIGHT in the response and will
    arrive when the bcaps form-params scrape or a paid Tyler integration lands.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, account_num, payload_json, fetched_at, last_error "
                    "FROM benton_assessment_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_BENTON_CACHE_TTL_HOURS):
                        payload = cached["payload_json"] or {}
                        payload["from_cache"] = True
                        payload["cached_age_hours"] = round(age.total_seconds() / 3600, 1)
                        return payload
        except Exception as e:
            print(f"[benton_assessment] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """
                SELECT MIN(account_num) AS account_num,
                       MIN(maptaxlot) AS maptaxlot,
                       MIN(map_number) AS map_number,
                       MIN(tax_code_area) AS tax_code_area,
                       STRING_AGG(DISTINCT party_name, '; ' ORDER BY party_name)
                           FILTER (WHERE party_name IS NOT NULL) AS owner_name,
                       COUNT(DISTINCT party_name)
                           FILTER (WHERE party_name IS NOT NULL) AS owner_count,
                       MIN(situs_addr1) AS situs_addr1,
                       MIN(situs_city) AS situs_city,
                       MIN(situs_state) AS situs_state,
                       MIN(situs_zip) AS situs_zip,
                       MIN(in_care_of) AS in_care_of,
                       MIN(mail_line1) AS mail_line1,
                       MIN(mail_line2) AS mail_line2,
                       MIN(mail_city) AS mail_city,
                       MIN(mail_state) AS mail_state,
                       MIN(mail_zip) AS mail_zip,
                       ROUND((ST_Area((MIN(geom))::geography) / 4046.8564224)::numeric, 3) AS acres
                  FROM parcels_benton
                 WHERE ortaxlot = %s
                """,
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row or not row["account_num"]:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_benton", "taxlot": taxlot}

    situs_addr1 = row["situs_addr1"]
    situs_address = None
    if situs_addr1 and situs_addr1 != "UNASSIGNED":
        situs_address = situs_addr1
        if row["situs_city"]:
            situs_address += f", {row['situs_city']}"

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "county_fips": "003",
        "account_number": row["account_num"],
        "maptaxlot": row["maptaxlot"],
        "map_number": row["map_number"],
        "tax_code_area": row["tax_code_area"],
        "source": "parcels_benton L1 inline (Benton GIS TaxlotOwners FeatureServer; owner-aggregated via STRING_AGG)",
        "snapshot": {
            "owner": {
                "names": row["owner_name"],
                "owner_count": row["owner_count"],
                "in_care_of": row["in_care_of"],
            },
            "situs": {
                "address": situs_address,
                "street1": situs_addr1 if situs_addr1 != "UNASSIGNED" else None,
                "city": row["situs_city"],
                "state": row["situs_state"],
                "zip": row["situs_zip"],
                "is_vacant_or_unaddressed": situs_addr1 == "UNASSIGNED",
            },
            "mailing": {
                "line1": row["mail_line1"],
                "line2": row["mail_line2"],
                "city": row["mail_city"],
                "state": row["mail_state"],
                "zip": row["mail_zip"],
            },
            "geometry": {
                "acres": float(row["acres"]) if row["acres"] is not None else None,
            },
        },
        "deep_links": {
            "assessment_portal": f"{_BENTON_ASSESS_BASE}/property-account-search/",
            "assessment_home": _BENTON_ASSESS_BASE,
        },
        "in_flight": [
            "Per-parcel deep link — assessment.bentoncountyor.gov property-account-search is a POST-only WordPress bcaps form with no GET deep-link surface. Portal landing is the verified working link until bcaps form-params are captured.",
            "Real Market Value / Assessed Value / sale history — NOT in Benton's L1 TaxlotOwners service. Resolved via assessment portal click-through (manual) until bcaps scrape lands or a paid Tyler integration is wired.",
            "Zoning — Benton ZoningService is a separate FeatureServer at gis.co.benton.or.us/.../ZoningService. Spatial join into the L2 fetcher is queued.",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO benton_assessment_cache (taxlot, account_num, payload_json, fetched_at, last_error)
                   VALUES (%s, %s, %s::jsonb, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       account_num = EXCLUDED.account_num,
                       payload_json = EXCLUDED.payload_json,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, row["account_num"], _json.dumps(payload)),
            )
            cn.commit()
    except Exception as e:
        print(f"[benton_assessment] cache write error: {e}")

    return payload


def fetch_benton_records(taxlot: str, force_refresh: bool = False) -> dict:
    """Benton recorded-documents / deed-chain for a taxlot.

    CURRENTLY IN FLIGHT — Benton runs a public recorded-document search at
    records.co.benton.or.us/Recording/, but the search interface is session-token
    gated (no clean GET deep link with pre-filled params). Returns current owner
    name (from L1 aggregation) + verified deep links to the recordings portal
    and the Records & Elections landing page, with honest IN FLIGHT framing.
    """
    if not taxlot or not isinstance(taxlot, str):
        return {"found": False, "reason": "Invalid taxlot"}
    taxlot = taxlot.strip().upper()

    if not force_refresh:
        try:
            with _conn() as cn, cn.cursor() as cur:
                cur.execute(
                    "SELECT taxlot, owner_name, documents_json, document_count, fetched_at "
                    "FROM benton_records_cache WHERE taxlot = %s",
                    (taxlot,),
                )
                cached = cur.fetchone()
                if cached:
                    age = _dt.now(_tz.utc) - cached["fetched_at"]
                    if age < _td(hours=_BENTON_CACHE_TTL_HOURS):
                        return {
                            "found": True,
                            "from_cache": True,
                            "cached_age_hours": round(age.total_seconds() / 3600, 1),
                            "taxlot": taxlot,
                            "owner_name": cached["owner_name"],
                            "snapshot": {"owner_now": cached["owner_name"]},
                            "documents": cached["documents_json"] or [],
                            "document_count": cached["document_count"] or 0,
                            "deep_links": {
                                "recordings_portal": _BENTON_RECORDS_PORTAL,
                                "records_index_landing": _BENTON_RECORDS_INDEX_LANDING,
                            },
                            "in_flight": [
                                "Structured scrape of records.co.benton.or.us/Recording/ — session-token gated portal; deep-link-only until session capture or paid integration lands.",
                            ],
                            "fetched_at": cached["fetched_at"].isoformat(),
                        }
        except Exception as e:
            print(f"[benton_records] cache read error: {e}")

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """SELECT MIN(account_num) AS account_num,
                          STRING_AGG(DISTINCT party_name, '; ' ORDER BY party_name)
                              FILTER (WHERE party_name IS NOT NULL) AS owner_name,
                          COUNT(DISTINCT party_name)
                              FILTER (WHERE party_name IS NOT NULL) AS owner_count,
                          MIN(situs_addr1) AS situs_addr1,
                          MIN(situs_city) AS situs_city
                     FROM parcels_benton
                    WHERE ortaxlot = %s""",
                (taxlot,),
            )
            row = cur.fetchone()
    except Exception as e:
        return {"found": False, "reason": f"DB error: {e}", "taxlot": taxlot}

    if not row or not row["account_num"]:
        return {"found": False, "reason": f"Taxlot {taxlot} not in parcels_benton", "taxlot": taxlot}

    payload = {
        "found": True,
        "from_cache": False,
        "taxlot": taxlot,
        "owner_name": row["owner_name"],
        "snapshot": {
            "owner_now": row["owner_name"],
            "owner_count": row["owner_count"],
            "situs_address": (
                row["situs_addr1"] + ((', ' + row["situs_city"]) if row["situs_city"] else '')
                if row["situs_addr1"] and row["situs_addr1"] != "UNASSIGNED"
                else None
            ),
        },
        "documents": [],
        "document_count": 0,
        "deep_links": {
            "recordings_portal": _BENTON_RECORDS_PORTAL,
            "records_index_landing": _BENTON_RECORDS_INDEX_LANDING,
            "assessment_portal": f"{_BENTON_ASSESS_BASE}/property-account-search/",
        },
        "in_flight": [
            "Full chain of title / deed history — Benton's records portal at records.co.benton.or.us/Recording/ is session-token gated, no public GET deep-link surface. Open paths: (a) session capture + structured scrape, (b) paid Tyler integration, (c) per-doc Clerk export request.",
            "Sales chain — same blocker. Most-recent ownership above is current as of last L1 refresh (107,988 owner-rows ingested 2026-05-27).",
        ],
        "fetched_at": _dt.now(_tz.utc).isoformat(),
    }

    try:
        with _conn() as cn, cn.cursor() as cur:
            cur.execute(
                """INSERT INTO benton_records_cache (taxlot, owner_name, documents_json, document_count, fetched_at, last_error)
                   VALUES (%s, %s, '[]'::jsonb, 0, NOW(), NULL)
                   ON CONFLICT (taxlot) DO UPDATE SET
                       owner_name = EXCLUDED.owner_name,
                       documents_json = EXCLUDED.documents_json,
                       document_count = EXCLUDED.document_count,
                       fetched_at = NOW(),
                       last_error = NULL""",
                (taxlot, row["owner_name"]),
            )
            cn.commit()
    except Exception as e:
        print(f"[benton_records] cache write error: {e}")

    return payload


def fetch_benton_permits(taxlot: str, force_refresh: bool = False) -> dict:
    """Benton permits — one-line wrapper around fetch_county_permits for FIPS 003.

    Benton participates in the Oregon state ePermitting portal at
    aca-oregon.accela.com/oregon (confirmed via cd.bentoncountyor.gov/electronic-
    permitting). Same deep-link-only IN FLIGHT pattern as Crook/Marion/Lane —
    server-side scrape gated on viewstate capture or Playwright.

    Brief reuse hypothesis VALIDATED for Benton: cd.bentoncountyor.gov links to
    the state Accela directly, so this is a clean wrapper and not a county-direct
    Accela (unlike Washington/Clackamas which run their own tenants).
    """
    result = fetch_county_permits(taxlot, "003", force_refresh)
    if isinstance(result, dict) and result.get("found"):
        result["routed_via"] = "oregon_state_accela"
        result["jurisdiction"] = "Benton County (Corvallis / Philomath / Adair Village / Monroe / unincorporated — Oregon state Accela)"
    return result


# =============================================================================
# LINN County L2/L3 — re-exported from linn_terra (separate module to dodge the
# five-parallel-session edit thrash on this file; functional contract identical
# to every other county). Place at end-of-file so callers `from .orb_db import
# fetch_linn_*` keep working even if Linn lands its own subpackage later.
# =============================================================================
from .linn_terra import (
    fetch_linn_assessment,
    fetch_linn_records,
    fetch_linn_permits,
)
