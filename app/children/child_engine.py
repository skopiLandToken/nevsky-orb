"""
child_engine — Shared execution engine for all ORB children.

Each child is a row in child_personas. This engine loads that row, applies
tier-based tool filtering, runs the Anthropic tool-use loop with rate-limit
fallback, and returns a reply.

Doctrine:
- Tier defines tool access. Override per-child via enabled_tools/disabled_tools.
- Sonnet 4.6 is default; rate-limit triggers Haiku fallback.
- All errors get sanitized before user-facing return.
- Child_engine NEVER writes through orb_db (read-only by design).
"""
import os
import json
import logging
from datetime import datetime, timezone
from typing import Optional
import psycopg
from psycopg.rows import dict_row
from anthropic import Anthropic

from .orb_db import (
    search_knowledge_store,
    get_knowledge_entry_full,
    get_active_users,
    get_tombstoned_users,
    get_blocklist,
    get_honeypot_hits,
    get_recent_yakov_tasks,
    get_site_plans,
    get_ai_spend_today,
)

logger = logging.getLogger(__name__)

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=30.0, max_retries=0)

MAX_TOOL_ITERATIONS = 4
MAX_TOOL_RESULT_CHARS = 2000


# =============================================================================
# TIER-TO-TOOL REGISTRY
# =============================================================================
# Lesson for Nevsky: this dict is THE doctrine for what each tier can do.
# Editing it changes authority across the entire system. Treat with care.
# Per-child override is possible via child_personas.enabled_tools / disabled_tools.
# =============================================================================

TIER_TOOLS = {
    "sovereign": [
        # Sophia only — full read across everything.
        "search_knowledge_store", "get_knowledge_entry_full",
        "get_active_users", "get_tombstoned_users",
        "get_blocklist", "get_honeypot_hits",
        "get_recent_yakov_tasks", "get_site_plans",
        "get_ai_spend_today",
        "web_search",
    ],
    "executive": [
        # C-suite (Ophelia, future C-suite). Cant see honeypot or full users list.
        "search_knowledge_store", "get_knowledge_entry_full",
        "get_active_users",
        "get_recent_yakov_tasks", "get_site_plans",
        "web_search",
    ],
    "staff": [
        # Internal team. Can search KB but not see system internals.
        "search_knowledge_store", "get_knowledge_entry_full",
        "web_search",
    ],
    "partner": [
        # Affiliates, Wade, external collaborators. KB only what is public/their scope.
        "search_knowledge_store",
        "web_search",
    ],
    "public": [
        # Outward-facing (Vesna social media). No internal data.
        "web_search",
    ],
    "honeypot": [
        # Decommissioned children. NO tools.
    ],
}


# =============================================================================
# TOOL DEFINITIONS — Anthropic API format
# =============================================================================

ALL_TOOL_SCHEMAS = {
    "web_search": {
        "type": "web_search_20250305",
        "name": "web_search",
    },
    "search_knowledge_store": {
        "name": "search_knowledge_store",
        "description": "Search Nevsky knowledge_store for entries by free-text query, content_type, and/or tags.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "content_type": {"type": "string", "enum": ["agreement", "session_note", "decision", "email", "document", "playbook", "profile", "research", "other"]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "default": 10}
            }
        }
    },
    "get_knowledge_entry_full": {
        "name": "get_knowledge_entry_full",
        "description": "Retrieve FULL content of one knowledge_store entry by id.",
        "input_schema": {"type": "object", "properties": {"entry_id": {"type": "string"}}, "required": ["entry_id"]}
    },
    "get_active_users": {
        "name": "get_active_users",
        "description": "All non-tombstoned users in SKOpi.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_tombstoned_users": {
        "name": "get_tombstoned_users",
        "description": "All tombstoned users with reasons.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_blocklist": {
        "name": "get_blocklist",
        "description": "personas_blocklist - actors blocked across all bots.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_honeypot_hits": {
        "name": "get_honeypot_hits",
        "description": "Recent contact attempts captured by the blocklist guard.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}
    },
    "get_recent_yakov_tasks": {
        "name": "get_recent_yakov_tasks",
        "description": "Recent automation tasks from yakov_tasks queue.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}}
    },
    "get_site_plans": {
        "name": "get_site_plans",
        "description": "Site plan analyses with lot counts and pricing.",
        "input_schema": {"type": "object", "properties": {}}
    },
    "get_ai_spend_today": {
        "name": "get_ai_spend_today",
        "description": "Today AI spend across all bots and Yakov, by model.",
        "input_schema": {"type": "object", "properties": {}}
    },
}

LOCAL_TOOL_FUNCTIONS = {
    "search_knowledge_store": search_knowledge_store,
    "get_knowledge_entry_full": get_knowledge_entry_full,
    "get_active_users": get_active_users,
    "get_tombstoned_users": get_tombstoned_users,
    "get_blocklist": get_blocklist,
    "get_honeypot_hits": get_honeypot_hits,
    "get_recent_yakov_tasks": get_recent_yakov_tasks,
    "get_site_plans": get_site_plans,
    "get_ai_spend_today": get_ai_spend_today,
}


# =============================================================================
# CHILD LOADER
# =============================================================================

def _conn():
    return psycopg.connect(DB_DSN, row_factory=dict_row)


def load_child(canonical_name: str) -> Optional[dict]:
    """Load a child config row from child_personas by canonical_name."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM child_personas WHERE canonical_name = %s",
                (canonical_name,),
            )
            row = cur.fetchone()
            return dict(row) if row else None
    except Exception as e:
        logger.error("load_child(%s) error: %s", canonical_name, e)
        return None


def resolve_tools_for_child(child: dict) -> list[dict]:
    """
    Compute the tool list for a child based on tier + per-child overrides.
    
    Lesson for Nevsky: composition over hardcoding.
    Tier provides defaults. Per-child enabled_tools EXPANDS access (sparingly).
    Per-child disabled_tools REMOVES access (more common — a partner child
    might have all partner-tier tools except web_search if they're untrusted).
    """
    tier = child.get("tier", "staff")
    base_tools = set(TIER_TOOLS.get(tier, []))

    enabled_extra = set(child.get("enabled_tools") or [])
    disabled = set(child.get("disabled_tools") or [])

    final_tool_names = (base_tools | enabled_extra) - disabled

    # Convert tool names to Anthropic API tool dicts
    return [ALL_TOOL_SCHEMAS[name] for name in final_tool_names if name in ALL_TOOL_SCHEMAS]


# =============================================================================
# EXECUTION ENGINE
# =============================================================================

def _execute_local_tool(name: str, tool_input: dict):
    """Execute a local tool (orb_db function) with truncation guard."""
    fn = LOCAL_TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = fn(**tool_input)
        result_json = json.dumps(result, default=str)
        if len(result_json) > MAX_TOOL_RESULT_CHARS:
            return {
                "_truncated": True,
                "_full_size": len(result_json),
                "_note": f"Result truncated to {MAX_TOOL_RESULT_CHARS} chars. Use get_knowledge_entry_full(entry_id) for specifics.",
                "preview": result_json[:MAX_TOOL_RESULT_CHARS],
            }
        return json.loads(result_json)
    except TypeError as e:
        return {"error": f"Tool argument error for {name}: {e}"}
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return {"error": f"Tool execution error: {e}"}


def _is_rate_limit_error(err) -> bool:
    s = str(err).lower()
    return "rate_limit" in s or "429" in s or "rate limit" in s


def _friendly_error(err) -> str:
    if _is_rate_limit_error(err):
        return ("I am currently rate-limited (Tier 1 ceiling). "
                "Try again in 60 seconds, or use a shorter question.")
    err_short = str(err)[:120]
    return f"I hit a technical snag. Yakov should check the logs. Quick detail: {err_short}"


def _select_model(child: dict, message: str) -> str:
    """Three tiers: light for trivial, opus for explicit escalation, deep default."""
    msg_lower = message.lower().strip()
    trivial = ["hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "sure"]
    if msg_lower in trivial or len(msg_lower) < 8:
        return child["model_light"]
    opus_triggers = ["go deep", "think hard", "deeply analyze", "use opus", "opus mode", "deep analysis", "think carefully", "maximum effort"]
    if any(t in msg_lower for t in opus_triggers):
        return child["model_opus"]
    return child["model_deep"]


def _save_insight(child: dict, user_message: str, reply: str):
    """Append Q&A to per-child insight log."""
    try:
        slug = child["canonical_name"]
        path = f"/opt/nevsky-dev/kb/{slug}_profile.jsonl"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "child": slug,
            "bound_user_uuid": str(child.get("bound_user_uuid") or ""),
            "question": user_message,
            "answer": reply,
        }
        with open(path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Could not save insight for %s: %s", child.get("canonical_name"), e)


async def _call_with_fallback(model: str, system: str, messages: list, tools: list, light_model: str):
    """Try requested model; on rate-limit fall back to light_model (Haiku)."""
    try:
        return client.messages.create(
            model=model, max_tokens=2500,
            system=system, messages=messages, tools=tools,
        ), model
    except Exception as e:
        if _is_rate_limit_error(e) and model != light_model:
            logger.warning("model=%s rate-limited, falling back to %s", model, light_model)
            return client.messages.create(
                model=light_model, max_tokens=2500,
                system=system, messages=messages, tools=tools,
            ), light_model
        raise


async def ask_child(canonical_name: str, user_message: str, conversation_history: list = None) -> str:
    """
    Main entry point. Load child config, run tool-use loop with fallback, return reply.
    
    Lesson for Nevsky: ONE function does the work for ALL children. Adding a new
    child means inserting a row, not adding a function. Adding a new tool means
    appending to ALL_TOOL_SCHEMAS and TIER_TOOLS, not editing every child file.
    """
    child = load_child(canonical_name)
    if not child:
        return f"Child '{canonical_name}' not found in child_personas table."

    # Honeypot tier returns a generic non-response — design intent.
    if child["tier"] == "honeypot":
        logger.info("honeypot hit: child=%s", canonical_name)
        return "[honeypot: silent]"

    if child.get("decommissioned_at"):
        logger.info("decommissioned child contacted: %s", canonical_name)
        return "[decommissioned]"

    tools = resolve_tools_for_child(child)
    history = conversation_history or []
    messages = history + [{"role": "user", "content": user_message}]
    model = _select_model(child, user_message)
    actual_model = model

    total_input = 0
    total_output = 0
    tool_calls_made = []

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            response, actual_model = await _call_with_fallback(
                model, child["system_prompt"], messages, tools, child["model_light"]
            )
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            if response.stop_reason in ("end_turn", "stop_sequence"):
                reply_parts = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
                reply = "\n".join(reply_parts).strip() or "I checked but could not find a clear answer."
                logger.info("child=%s model=%s iters=%d in=%d out=%d tools=%s",
                            canonical_name, actual_model, iteration + 1, total_input, total_output, tool_calls_made)
                _save_insight(child, user_message, reply)
                return reply

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_calls_made.append(block.name)
                        if block.name == "web_search":
                            continue
                        result = _execute_local_tool(block.name, block.input or {})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                    continue
                continue

            logger.warning("child=%s unexpected stop_reason: %s", canonical_name, response.stop_reason)
            reply_parts = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
            return "\n".join(reply_parts).strip() or "I encountered an unexpected stop. Please try again."

        logger.warning("child=%s hit MAX_TOOL_ITERATIONS=%d", canonical_name, MAX_TOOL_ITERATIONS)
        return "I needed too many lookups to answer this — let's narrow the question."

    except Exception as e:
        logger.error("child=%s error: %s", canonical_name, e)
        return _friendly_error(e)


def is_owner(child: dict, telegram_user_id: str) -> bool:
    """Check if a Telegram user is the bound owner of this child."""
    return str(child.get("bound_telegram_id") or "") == str(telegram_user_id)


def get_intro(canonical_name: str) -> str:
    """Return the intro_message for a child."""
    child = load_child(canonical_name)
    if not child:
        return f"[child '{canonical_name}' not found]"
    return child["intro_message"]
