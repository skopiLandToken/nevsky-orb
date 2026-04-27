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
