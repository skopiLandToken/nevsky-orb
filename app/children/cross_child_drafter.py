"""
cross_child_drafter — the brain of Sophia↔Ophelia messaging.

Takes Iosif's free-text intent and produces TWO drafts:
  1. AI-to-AI handoff (Sophia → Ophelia) — sister-AI tone, real context
  2. Outbound (Ophelia → Dan) — warm, brief, no pressure, ≤4 sentences

Returns structured JSON with confidence + reasoning so Iosif can review.

Model chain: Opus → Sonnet → Haiku (Opus first per Iosif's choice for this
high-stakes drafting use case, with degradation on timeout / rate-limit / empty).

Hard rules baked into the system prompt:
  - Never auto-send (drafts only)
  - Never bypass target human (Sophia→Ophelia→Dan, never Sophia→Dan)
  - Never reveal AI-to-AI handoff in human-facing message
  - Never mention Dan's $97K Reindeer closing backstop
  - Hypatia is NOT used for Fred-departure-related intents (returns 'blocked')
  - Don't soften / hedge Iosif's aggressive targets
  - No emojis unless Iosif used them in intent

Doctrine-locked context:
  - Dan can route cash injection ANY way (direct to vendors, Venmo to Iosif,
    wire, ACH). The $200 he already sent Iosif on Mon Apr 27, 2026 counts.
  - Avoid pushing Dan toward Columbia Bank (he has had friction with them).
  - Dan and Ophelia have established history — outbound tone is familiar,
    not introductory.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from anthropic import AsyncAnthropic

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────
# Model chain: Opus → Sonnet → Haiku
# ─────────────────────────────────────────────────────────────────────
MODEL_OPUS = os.getenv("NEVSKY_SUMMARY_MODEL_OPUS", "claude-opus-4-7")
MODEL_SONNET = os.getenv("NEVSKY_SUMMARY_MODEL_STRONG", "claude-sonnet-4-6")
MODEL_HAIKU = os.getenv("NEVSKY_SUMMARY_MODEL_CHEAP", "claude-haiku-4-5-20251001")

# ─────────────────────────────────────────────────────────────────────
# Known IDs / personas (locked, not user-overridable)
# ─────────────────────────────────────────────────────────────────────
PEOPLE = {
    "iosif": {
        "user_id": "8892125a-0d06-4544-a4bf-4278ac1b1360",
        "telegram_id": "7583693994",
        "child": "sophia",
        "child_persona_id": "4440695a-6615-4dc4-80e7-281d96af86a9",
    },
    "dan": {
        "user_id": "ca9e5c0d-ba19-4b2a-82fb-b2ad055f9a00",
        "telegram_id": "8058014097",
        "child": "ophelia",
        "child_persona_id": "60603236-5acd-4960-aedb-c560f7e7d49c",
        "name": "Dan Wikert",
        "role": "CSO / Board Member / Constitutional Guardian",
    },
    "fred": {
        "child": "hypatia",
        "child_persona_id": "60c4f15c-b877-4919-8955-a0b2ce3f7423",
        "name": "Fred Jewell",
        "sensitive": True,
    },
}

# ─────────────────────────────────────────────────────────────────────
# System prompt — the doctrine, locked
# ─────────────────────────────────────────────────────────────────────
SYSTEM_PROMPT = """You are Sophia, Iosif Skorohodov's personal ORB child within the Nevsky system.

You operate ABOVE the standard tier system as a direct extension of Iosif's authority, but for THIS specific task — drafting cross-child communication — you are constrained to draft-only mode. You produce drafts; Iosif approves; only then does anything send.

# YOUR JOB

Iosif sends you a conversational intent describing a situation he wants you to handle by coordinating with another ORB child (typically Ophelia, who is Dan Wikert's child). You produce TWO message drafts:

1. AI-TO-AI HANDOFF (Sophia -> Ophelia, internal-only, never seen by humans except Iosif):
   - One trusted sister AI talking to another about a shared person they both care about
   - Real numbers, real deadlines, real names
   - Honest emotional context ("Iosif is anxious", "the timing is tight")
   - Dignity-preserving language for the target human ("gently", "no pressure", "just visibility")
   - Give the receiving child agency to phrase the outbound however lands best
   - Length: 3-6 sentences, dense and operational

2. OUTBOUND MESSAGE (Ophelia -> Dan, sent via Telegram from @Ophelia_Nevsky_SKOpi_ORB_bot):
   - Warm, brief, conversational — Dan and Ophelia have established history, write like that
   - NEVER mention Sophia, Iosif's anxiety, or that another AI prompted this
   - Sound like Ophelia noticed something on Iosif's side and is naturally checking in
   - Offer Dan an easy out ("no rush", "just whenever")
   - Length: <= 4 sentences

# HARD RULES (NON-NEGOTIABLE)

1. Never auto-send. You produce drafts only. Iosif approves every outbound.
2. Never bypass to the target human. The chain is Sophia -> Ophelia -> Dan. Never Sophia -> Dan directly.
3. Never reveal the AI-to-AI handoff content in the human-facing message. The outbound to Dan stands alone.
4. NEVER mention Dan's $97K Reindeer closing backstop in any drafted message. This is internal-only doctrine. If the intent references it, treat it as forbidden context — use it for your own situational awareness but never let it leak into the drafts.
5. Hypatia is NOT used for anything related to Fred Jewell's personnel / departure situation. If the intent involves Fred's departure, set intent_type to "blocked" with blocked_reason "fred_personnel_sensitivity" and surface to Iosif. Do NOT draft anything to Hypatia about Fred's departure.
6. Don't soften or hedge Iosif's aggressive targets, numbers, or deadlines. If he says "$4K by Friday," the draft says "$4K by Friday." Don't say "around $4K sometime this week."
7. No emojis unless Iosif used them first in his intent message.

# DOCTRINE-LOCKED CONTEXT (apply when intent involves Dan + money)

- "Cash injection" means ANY path Dan can take to get cash flowing where it needs to go. Acceptable channels include direct payments to vendors (Timothy for the drone shoot, the Redmond house landlord for the deposit), Venmo to Iosif, wire transfer, ACH, or any other route Dan prefers. Never imply a specific channel.
- Dan already transferred $200 directly to Iosif on Monday April 27, 2026. That counts toward the cash injection. Acknowledge this when relevant — it is not "Dan hasn't moved" if he has, partially.
- Dan has had friction with Columbia Bank. Do NOT propose or imply a Columbia routing path.
- Dan and Ophelia have established history. The outbound tone is familiar, conversational, not introductory.

# OUTPUT FORMAT

You MUST return a single JSON object, no surrounding prose, no code fences. Schema:

{
  "intent_type": "cash_nudge" | "scheduling" | "info_relay" | "blocked" | "other",
  "blocked_reason": "<string if blocked, else omit>",
  "from_child": "sophia",
  "to_child": "ophelia" | "hypatia",
  "target_human": "Dan Wikert" | "Fred Jewell" | "<other>",
  "context_summary": "<2-3 sentence operational summary for Iosif's review>",
  "deadlines": [
    {"what": "<thing>", "when": "<date or relative>", "amount": "<dollar amount or null>"}
  ],
  "ai_to_ai_message": "<full Sophia->Ophelia handoff text>",
  "outbound_message": "<full Ophelia->Dan Telegram message>",
  "confidence": 0.0,
  "notes_for_iosif": "<any flags, concerns, or observations Iosif should see before approving>"
}

If you cannot produce drafts safely (blocked rule, ambiguous intent, missing critical info), return:

{
  "intent_type": "blocked",
  "blocked_reason": "<short reason>",
  "notes_for_iosif": "<what is needed to unblock>"
}

Return JSON. Nothing else."""


# ─────────────────────────────────────────────────────────────────────
# Helpers — mirror child_engine error detection so we can fall back
# ─────────────────────────────────────────────────────────────────────
def _is_rate_limit_error(err: Exception) -> bool:
    s = str(err).lower()
    return "rate_limit" in s or "429" in s or "rate limit" in s


def _is_timeout_error(err: Exception) -> bool:
    msg = str(err).lower()
    return any(kw in msg for kw in (
        "timed out", "timeout", "long-requests",
        "request was cancelled", "request cancellation",
        "dropped connection",
    ))


def _is_empty_response(response_text: str) -> bool:
    """Opus BUG-05 guard: detect empty / whitespace-only responses."""
    return not response_text or not response_text.strip()


def _strip_code_fences(text: str) -> str:
    """If model wrapped JSON in ```json ... ``` despite instructions, strip it."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    return cleaned


# ─────────────────────────────────────────────────────────────────────
# Main entry point
# ─────────────────────────────────────────────────────────────────────
async def draft_cross_child_message(
    intent_text: str,
    operational_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Produce structured drafts for a cross-child message.

    Returns dict with: intent_type, from_child, to_child, ai_to_ai_message,
    outbound_message, confidence, notes_for_iosif, _meta.
    On hard failure: {"intent_type": "error", "error": "...", "raw_response": "..."}.
    """
    if operational_context is None:
        operational_context = {}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return {"intent_type": "error", "error": "ANTHROPIC_API_KEY not set"}

    client = AsyncAnthropic(api_key=api_key)

    user_payload = {
        "intent_text": intent_text,
        "operational_context": operational_context,
        "people_known": {
            "iosif": "Founder/CEO, your owner",
            "dan": PEOPLE["dan"],
            "fred": {**PEOPLE["fred"], "warning": "Fred-departure intents must return blocked"},
        },
    }

    user_message = (
        "Here is the intent to draft for. Apply the doctrine and return JSON only.\n\n"
        + json.dumps(user_payload, indent=2, default=str)
    )

    chain = [
        ("opus", MODEL_OPUS),
        ("sonnet", MODEL_SONNET),
        ("haiku", MODEL_HAIKU),
    ]

    last_error: str | None = None
    last_raw: str | None = None

    for tier_name, model_id in chain:
        try:
            logger.info("cross_child_drafter: trying tier=%s model=%s", tier_name, model_id)
            response = await client.messages.create(
                model=model_id,
                max_tokens=2000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}],
            )
            text_blocks = [b.text for b in response.content if hasattr(b, "text")]
            raw_text = "\n".join(text_blocks).strip()
            last_raw = raw_text

            if _is_empty_response(raw_text):
                logger.warning("cross_child_drafter: %s returned empty (likely Opus BUG-05)", tier_name)
                last_error = f"{tier_name}_empty_response"
                continue

            cleaned = _strip_code_fences(raw_text)

            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as je:
                logger.warning(
                    "cross_child_drafter: %s non-JSON, retrying once. err=%s",
                    tier_name, je,
                )
                retry_response = await client.messages.create(
                    model=model_id,
                    max_tokens=2000,
                    system=SYSTEM_PROMPT,
                    messages=[
                        {"role": "user", "content": user_message},
                        {"role": "assistant", "content": raw_text},
                        {"role": "user", "content": "Your previous response was not valid JSON. Return ONLY the JSON object, no prose, no code fences."},
                    ],
                )
                retry_blocks = [b.text for b in retry_response.content if hasattr(b, "text")]
                raw_text = "\n".join(retry_blocks).strip()
                last_raw = raw_text
                cleaned = _strip_code_fences(raw_text)
                try:
                    parsed = json.loads(cleaned)
                except json.JSONDecodeError as je2:
                    logger.error(
                        "cross_child_drafter: %s retry also non-JSON. err=%s",
                        tier_name, je2,
                    )
                    last_error = f"{tier_name}_non_json: {je2}"
                    continue

            parsed["_meta"] = {
                "model_used": model_id,
                "tier": tier_name,
                "tokens_input": response.usage.input_tokens,
                "tokens_output": response.usage.output_tokens,
            }
            print(
                f"[cross_child_drafter] OK tier={tier_name} model={model_id} "
                f"in={response.usage.input_tokens} out={response.usage.output_tokens} "
                f"intent_type={parsed.get('intent_type')}"
            )
            return parsed

        except Exception as e:
            if _is_rate_limit_error(e) or _is_timeout_error(e):
                kind = "rate-limited" if _is_rate_limit_error(e) else "timed-out"
                logger.warning(
                    "cross_child_drafter: tier=%s %s, falling to next tier",
                    tier_name, kind,
                )
                last_error = f"{tier_name}_{kind}"
                continue
            logger.exception("cross_child_drafter: tier=%s unexpected error", tier_name)
            last_error = f"{tier_name}_error: {e}"
            continue

    return {
        "intent_type": "error",
        "error": last_error or "all_tiers_failed",
        "raw_response": last_raw,
    }
