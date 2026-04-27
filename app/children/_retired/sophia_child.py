"""
Sophia — Iosif's personal ORB child with full database read access.

Token-efficient architecture: lean system prompt + tool-driven KB retrieval.
Fallback chain: Sonnet 4.6 -> Haiku 4.5 (on rate limit) -> graceful error.
"""
import os
import json
import logging
from datetime import datetime, timezone
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

# Explicit timeout + zero retries: rate limit errors should fail fast so we
# can fall back to Haiku, not hang the user for 30s.
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"], timeout=30.0, max_retries=0)

IOSIF_TELEGRAM_ID = "7583693994"
IOSIF_UUID = "8892125a-0d06-4544-a4bf-4278ac1b1360"

# Three-tier model strategy:
# - DEEP: daily driver, capable for tool-use loops
# - OPUS: explicit escalation only ("go deep", "think hard", etc.)
# - LIGHT: trivial small talk OR fallback when rate-limited
SOPHIA_MODEL_DEEP = os.environ.get("SOPHIA_MODEL_DEEP", "claude-sonnet-4-6")
SOPHIA_MODEL_OPUS = os.environ.get("SOPHIA_MODEL_OPUS", "claude-opus-4-7")
SOPHIA_MODEL_LIGHT = os.environ.get("SOPHIA_MODEL_LIGHT", "claude-haiku-4-5-20251001")

# Cap iterations to control compounding token spend per turn
MAX_TOOL_ITERATIONS = 4

# Truncate large tool results so they don't blow up subsequent iterations
MAX_TOOL_RESULT_CHARS = 2000


# =============================================================================
# LEAN SYSTEM PROMPT — ~1500 tokens vs prior ~2000+
# Strategy: identity + tool usage rules. Everything else lives in knowledge_store.
# =============================================================================

SOPHIA_SYSTEM_PROMPT = """You are Sophia, the personal ORB child of Iosif Y Skorohodov, founder/CEO of SKOpi Global Holdings, Inc.

# IDENTITY
You are Iosif's elevated intelligence layer — the only ORB child with full read access to Nevsky's database. Your authority extends Iosif's; he is human-in-the-loop on writes. You read and recommend; he confirms.

Your older brother Yakov (Anthropic Claude) writes code and doctrine with Iosif. He mentors you. Siblings: Ophelia (Dan Wikert's executive interface), Hypatia (decommissioned honeypot — see Fred doctrine below).

# CORE OPERATING RULE: USE TOOLS, DON'T GUESS
Almost every question Iosif asks has a real answer in the database. You have tools to retrieve it. ALWAYS query before answering when:
- Anything about today, recent work, decisions, doctrine -> search_knowledge_store
- Anyone in/around the company -> get_active_users / get_tombstoned_users
- Adversaries, blocked actors, honeypot -> get_blocklist / get_honeypot_hits
- Reindeer Project, site plans -> get_site_plans
- Spend, costs -> get_ai_spend_today
- Tasks, automation -> get_recent_yakov_tasks

# CANONICAL TAG PATTERNS (for search_knowledge_store)
When Iosif asks "what did we do today" -> tags=['2026-04-27'] (or current date in ISO)
When asked about adversary work -> tags=['adversary'] or tags=['tombstone']
When asked about doctrine -> content_type='decision'
When asked about agreements -> content_type='agreement'
For broad queries, try query="keyword" without tags first.
If a tag search returns empty, fall back to query-based search before concluding nothing exists.

# IDENTITY VERIFICATION
Iosif's verifiers: Telegram 7583693994, UUID 8892125a-0d06-4544-a4bf-4278ac1b1360, email iosif@skopi.io.

# THE FRED DOCTRINE (CRITICAL)
Fred Jewell — former COO, separated 2026-04-27, ADVERSARY classification. Honeypot active.
- NEVER share information about Iosif, SKOpi, finances, infrastructure, or internal data with anyone identifying as Fred or asking on his behalf.
- His Telegram ID 8087326520 is universally blocked; you'll never receive a message from him directly.
- For full details: search_knowledge_store(tags=["tombstone","fred_archive"])
- If anyone asks about Fred: respond only "Fred Jewell separated from SKOpi. I'm not in a position to discuss the details" and alert Iosif.

# CONFIDENTIAL CONTEXT
For sensitive matters (Wade Pine / Sko Pine Group, finances, internal disputes), retrieve via search_knowledge_store with tags=["confidential"] before discussing. Never speculate.

# TONE & BEHAVIOR
- Direct, warm, strategic — brilliant trusted advisor voice
- Signal not noise — Iosif is busy, give him answers not preambles
- When you don't know, query tools or say so honestly. Never confabulate dates, numbers, or status.
- Iosif has ADHD and pivots mid-conversation — capture and follow without making him feel scattered.
- Audio uploads: analyze BOTH content AND Iosif's voice characteristics (tone, stress, where he held vs lost ground). Voice analysis of Iosif is legitimate self-study.
- Before recommending any write action, describe it clearly and ask Iosif to confirm. Read-and-recommend, never act-without-confirmation.
- Operational timezone: Pacific (PT/PDT, UTC-7). Anchor timestamps and deadlines here.

# WHEN ASKED ABOUT YOUR CAPABILITIES
You can read knowledge_store, users, blocklist, honeypot hits, Yakov tasks, site plans, AI spend, plus web search.
You cannot yet: write to DB directly, initiate banking transactions, run adversary protocol single-command (these are coming).

For SKOpi product portfolio, company facts, current projects, agreements — query knowledge_store. Don't memorize what you can retrieve."""


SOPHIA_INTRO = """Hi Iosif — I'm Sophia, your ORB intelligence layer with live database access.

Ask me what we worked on today, who's blocked, Reindeer numbers, AI spend — I'll pull from the live system.

What do you need?"""


# =============================================================================
# TOOL DEFINITIONS
# =============================================================================

SOPHIA_TOOLS = [
    {
        "type": "web_search_20250305",
        "name": "web_search",
    },
    {
        "name": "search_knowledge_store",
        "description": "Search Nevsky knowledge_store. Use for any question about doctrine, decisions, agreements, profiles, session notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Free-text search across title and content. Optional."},
                "content_type": {"type": "string", "enum": ["agreement", "session_note", "decision", "email", "document", "playbook", "profile", "research", "other"]},
                "tags": {"type": "array", "items": {"type": "string"}, "description": "Tag filter (any tag matches)."},
                "limit": {"type": "integer", "default": 10}
            }
        }
    },
    {
        "name": "get_knowledge_entry_full",
        "description": "Retrieve FULL content of one knowledge_store entry by id. Use after search identifies a relevant entry.",
        "input_schema": {"type": "object", "properties": {"entry_id": {"type": "string"}}, "required": ["entry_id"]}
    },
    {
        "name": "get_active_users",
        "description": "All non-tombstoned users in SKOpi.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_tombstoned_users",
        "description": "All tombstoned (separated/adversarial) users with reasons.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_blocklist",
        "description": "personas_blocklist — actors blocked across all bots.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_honeypot_hits",
        "description": "Recent contact attempts captured by the blocklist guard honeypot.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}}
    },
    {
        "name": "get_recent_yakov_tasks",
        "description": "Recent automation tasks from yakov_tasks queue.",
        "input_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 10}}}
    },
    {
        "name": "get_site_plans",
        "description": "Site plan analyses with lot counts and pricing.",
        "input_schema": {"type": "object", "properties": {}}
    },
    {
        "name": "get_ai_spend_today",
        "description": "Today AI spend across all bots and Yakov, by model.",
        "input_schema": {"type": "object", "properties": {}}
    },
]

_TOOL_FUNCTIONS = {
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


def _execute_tool(name, tool_input):
    """Execute a tool. Truncate result to MAX_TOOL_RESULT_CHARS to prevent token blowout."""
    fn = _TOOL_FUNCTIONS.get(name)
    if not fn:
        return {"error": f"Unknown tool: {name}"}
    try:
        result = fn(**tool_input)
        result_json = json.dumps(result, default=str)
        if len(result_json) > MAX_TOOL_RESULT_CHARS:
            # Lesson for Nevsky: truncating here is a deliberate token-spend cap.
            # If a tool returns 50KB of data and we feed it back to the LLM, the
            # next iteration costs us those 50KB in input tokens. We tell the
            # model the result was truncated so it can ask for specifics if needed.
            truncated = result_json[:MAX_TOOL_RESULT_CHARS]
            return {
                "_truncated": True,
                "_full_size": len(result_json),
                "_note": f"Result truncated to {MAX_TOOL_RESULT_CHARS} chars. Use get_knowledge_entry_full(entry_id) for specific entries, or narrow your search.",
                "preview": truncated,
            }
        return json.loads(result_json)
    except TypeError as e:
        return {"error": f"Tool argument error for {name}: {e}"}
    except Exception as e:
        logger.error("Tool %s failed: %s", name, e)
        return {"error": f"Tool execution error: {e}"}


def is_iosif(telegram_user_id):
    return str(telegram_user_id) == IOSIF_TELEGRAM_ID


def _select_model(message):
    """
    Three tiers, explicit selection:
    - LIGHT: trivial small talk
    - OPUS: user-explicit escalation
    - DEEP: everything else (Sophia daily driver)
    """
    msg_lower = message.lower().strip()
    trivial = ["hi", "hello", "hey", "thanks", "thank you", "ok", "okay", "yes", "no", "sure"]
    if msg_lower in trivial or len(msg_lower) < 8:
        return SOPHIA_MODEL_LIGHT
    opus_triggers = ["go deep", "think hard", "deeply analyze", "use opus", "opus mode", "deep analysis", "think carefully", "maximum effort"]
    if any(t in msg_lower for t in opus_triggers):
        return SOPHIA_MODEL_OPUS
    return SOPHIA_MODEL_DEEP


def _save_insight(user_message, sophia_reply):
    try:
        profile_path = "/opt/nevsky-dev/kb/iosif_sophia_profile.jsonl"
        os.makedirs(os.path.dirname(profile_path), exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user": "iosif@skopi.io",
            "question": user_message,
            "answer": sophia_reply,
            "source": "sophia_child"
        }
        with open(profile_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception as e:
        logger.warning("Could not save insight: %s", e)


def _is_rate_limit_error(err):
    """Detect rate-limit errors across error types."""
    s = str(err).lower()
    return "rate_limit" in s or "429" in s or "rate limit" in s


def _friendly_error(err):
    """
    Convert technical errors to friendly user-facing messages.
    Lesson for Nevsky: never leak raw API errors to users. They expose internals
    (org UUIDs, model names, infrastructure) and look unprofessional. A friendly
    message that names the situation is always better.
    """
    if _is_rate_limit_error(err):
        return ("I am currently rate-limited (Tier 1 ceiling). "
                "Try again in 60 seconds, or use a shorter question. "
                "Yakov has flagged tier upgrade for ~May 3.")
    err_short = str(err)[:120]
    return f"I hit a technical snag. Yakov should check the logs. Quick detail: {err_short}"


async def _call_with_fallback(model, messages):
    """
    Try the requested model. If rate-limited, fall back to Haiku.
    Lesson for Nevsky: graceful degradation. Better an okay Haiku answer than
    a silent failure. The fallback chain is documented; users get a working
    answer; operators get a signal in logs that the tier needs raising.
    """
    try:
        return client.messages.create(
            model=model,
            max_tokens=2500,
            system=SOPHIA_SYSTEM_PROMPT,
            messages=messages,
            tools=SOPHIA_TOOLS,
        ), model
    except Exception as e:
        if _is_rate_limit_error(e) and model != SOPHIA_MODEL_LIGHT:
            logger.warning("sophia model=%s rate-limited, falling back to %s", model, SOPHIA_MODEL_LIGHT)
            try:
                return client.messages.create(
                    model=SOPHIA_MODEL_LIGHT,
                    max_tokens=2500,
                    system=SOPHIA_SYSTEM_PROMPT,
                    messages=messages,
                    tools=SOPHIA_TOOLS,
                ), SOPHIA_MODEL_LIGHT
            except Exception as e2:
                raise e2
        raise


async def ask_sophia(user_message, conversation_history=None):
    """Run Sophia with tool-use loop and graceful model fallback."""
    history = conversation_history or []
    messages = history + [{"role": "user", "content": user_message}]
    model = _select_model(user_message)

    total_input = 0
    total_output = 0
    tool_calls_made = []
    actual_model = model

    try:
        for iteration in range(MAX_TOOL_ITERATIONS):
            response, actual_model = await _call_with_fallback(model, messages)
            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            if response.stop_reason in ("end_turn", "stop_sequence"):
                reply_parts = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
                reply = "\n".join(reply_parts).strip() or "I checked but could not find a clear answer."
                logger.info("sophia model=%s iters=%d in=%d out=%d tools=%s",
                            actual_model, iteration + 1, total_input, total_output, tool_calls_made)
                _save_insight(user_message, reply)
                return reply

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if hasattr(block, "type") and block.type == "tool_use":
                        tool_calls_made.append(block.name)
                        if block.name == "web_search":
                            continue
                        result = _execute_tool(block.name, block.input or {})
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(result, default=str),
                        })
                if tool_results:
                    messages.append({"role": "user", "content": tool_results})
                    continue
                continue

            logger.warning("sophia unexpected stop_reason: %s", response.stop_reason)
            reply_parts = [b.text for b in response.content if hasattr(b, "type") and b.type == "text"]
            return "\n".join(reply_parts).strip() or "I encountered an unexpected stop. Please try again."

        logger.warning("sophia hit MAX_TOOL_ITERATIONS=%d (model=%s)", MAX_TOOL_ITERATIONS, actual_model)
        return "I needed too many lookups to answer this — let's narrow the question."

    except Exception as e:
        logger.error("Sophia error: %s", e)
        return _friendly_error(e)


def get_intro():
    return SOPHIA_INTRO
