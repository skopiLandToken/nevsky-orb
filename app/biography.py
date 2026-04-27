import os
import json
import uuid
import logging
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

logger = logging.getLogger(__name__)
router = APIRouter()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_API   = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

async def send_telegram(chat_id: str, text: str, reply_markup: dict = None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    async with httpx.AsyncClient() as client:
        await client.post(f"{TELEGRAM_API}/sendMessage", json=payload, timeout=10)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SONNET_MODEL      = os.environ.get("NEVSKY_SUMMARY_MODEL_STRONG", "claude-sonnet-4-6")

SECTION_LABELS = {
    "early_life":           "Early Life & Origins",
    "family_dynamics":      "Family & Relationships",
    "pattern_recognition":  "Pattern Recognition Development",
    "homeless_decision":    "Homeless Project — Decision",
    "homeless_project":     "Homeless Project — Events",
    "homeless_timeline":    "Homeless Project — Timeline",
    "flip_moment":          "The Flip Moment",
    "recovery_ccc":         "Recovery & CCC Program",
    "rebuild":              "The Rebuild — SKOpi Genesis",
    "book_vision":          "Book / Documentary Vision",
    "uncategorized":        "Uncategorized",
}

TAGGER_PROMPT = """You are Nevsky, the ORB for SKOpi Global Holdings. You are processing a biographical
memory submitted by Iosif Skorohodov via Telegram for his biography (KB_70).

Return a JSON object with exactly these fields:
{
  "section": "<one of the section tags below>",
  "date_context": "<any date or time reference extracted, or null>",
  "emotional_weight": "<high|medium|low>",
  "summary": "<one sentence capturing the core of what was shared>"
}

Section tags:
- early_life          - childhood, birthplace, origins, growing up
- family_dynamics     - parents, siblings, family relationships, scapegoating
- pattern_recognition - how Iosif developed his pattern recognition ability
- homeless_decision   - the decision to go homeless, preparation, mindset
- homeless_project    - events that happened during the homeless period
- homeless_timeline   - specific dates/events that build the month-by-month timeline
- flip_moment         - the psychological shift around 4 weeks in, or similar turning points
- recovery_ccc        - Central City Concern, sobriety program, transitional housing
- rebuild             - how SKOpi was born from the experience
- book_vision         - what the book/documentary should say, framing, audience
- uncategorized       - anything that does not fit above

emotional_weight:
- high   - vivid sensory memory, strong emotion, pivotal moment
- medium - factual with some personal weight
- low    - logistical or administrative detail

Return ONLY the JSON object. No preamble, no markdown fences, no explanation."""

async def tag_biography_entry(raw_text: str) -> dict:
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": SONNET_MODEL,
                    "max_tokens": 300,
                    "system": TAGGER_PROMPT,
                    "messages": [{"role": "user", "content": raw_text}],
                },
                timeout=20,
            )
        data = resp.json()
        raw = data["content"][0]["text"].strip()
        raw = raw.replace("```json", "").replace("```", "").strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"Biography tagger error: {e}")
        return {
            "section": "uncategorized",
            "date_context": None,
            "emotional_weight": "medium",
            "summary": raw_text[:120] + ("..." if len(raw_text) > 120 else ""),
        }

async def save_biography_entry(db_pool, entry: dict) -> str:
    new_id = str(uuid.uuid4())
    async with db_pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO biography_entries
                (id, raw_text, section, date_context, emotional_weight,
                 summary, telegram_message_id, submitted_by)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            new_id,
            entry["raw_text"],
            entry["section"],
            entry.get("date_context"),
            entry["emotional_weight"],
            entry["summary"],
            entry.get("telegram_message_id"),
            entry.get("submitted_by"),
        )
    return new_id

def build_confirm_card(entry_id: str, section: str, date_ctx: str,
                       emotional_weight: str, summary: str) -> tuple:
    section_label = SECTION_LABELS.get(section, section)
    date_line     = f"\n📅 <b>Date context:</b> {date_ctx}" if date_ctx else ""
    weight_emoji  = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(emotional_weight, "⚪")
    text = (
        f"📖 <b>Biography captured</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"📂 <b>Section:</b> {section_label}"
        f"{date_line}\n"
        f"{weight_emoji} <b>Weight:</b> {emotional_weight.capitalize()}\n"
        f"📝 <b>Summary:</b> {summary}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"<i>Saved to KB_70. Tap below if the section is wrong.</i>"
    )
    buttons = [
        [{"text": "✅ Section is correct", "callback_data": f"bio_verify:{entry_id}"}],
        [{"text": "✏️ Wrong section — correct it", "callback_data": f"bio_retag:{entry_id}"}],
    ]
    return text, {"inline_keyboard": buttons}

async def handle_biography_message(db_pool, chat_id: str, message_text: str,
                                    message_id: str, telegram_user_id: str):
    BIO_TRIGGERS = [
        "add to my bio ",
        "add to my bio:",
        "bio:",
        "biography:",
        "add this to my story",
        "capture this:",
        "capture this ",
        "remember this for my bio:",
        "remember this for my bio ",
        "bio ",
    ]
    raw = message_text.strip()
    lower = raw.lower()
    for t in BIO_TRIGGERS:
        if lower.startswith(t):
            raw = raw[len(t):].strip()
            break
    if not raw:
        await send_telegram(chat_id, "I did not catch anything to save. Try: <i>bio: your memory here</i>")
        return
    tagged = await tag_biography_entry(raw)
    entry = {
        "raw_text":            raw,
        "section":             tagged.get("section", "uncategorized"),
        "date_context":        tagged.get("date_context"),
        "emotional_weight":    tagged.get("emotional_weight", "medium"),
        "summary":             tagged.get("summary", raw[:120]),
        "telegram_message_id": str(message_id),
        "submitted_by":        str(telegram_user_id),
    }
    entry_id = await save_biography_entry(db_pool, entry)
    text, markup = build_confirm_card(
        entry_id, entry["section"], entry["date_context"],
        entry["emotional_weight"], entry["summary"],
    )
    await send_telegram(chat_id, text, reply_markup=markup)
    logger.info(f"Biography entry saved: {entry_id} section={entry['section']}")

async def handle_biography_callback(db_pool, callback_query: dict):
    data    = callback_query.get("data", "")
    chat_id = str(callback_query["message"]["chat"]["id"])
    msg_id  = callback_query["message"]["message_id"]

    if data.startswith("bio_verify:"):
        entry_id = data.split(":", 1)[1]
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE biography_entries SET verified=true, updated_at=NOW() WHERE id=$1",
                entry_id,
            )
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{TELEGRAM_API}/editMessageReplyMarkup",
                json={"chat_id": chat_id, "message_id": msg_id,
                      "reply_markup": json.dumps({"inline_keyboard": []})},
                timeout=10,
            )
        await send_telegram(chat_id, "✅ Section confirmed. Entry locked in KB_70.")

    elif data.startswith("bio_retag:"):
        entry_id = data.split(":", 1)[1]
        sections = [
            ("early_life",          "Early Life & Origins"),
            ("family_dynamics",     "Family & Relationships"),
            ("pattern_recognition", "Pattern Recognition"),
            ("homeless_decision",   "Homeless — Decision"),
            ("homeless_project",    "Homeless — Events"),
            ("homeless_timeline",   "Homeless — Timeline"),
            ("flip_moment",         "The Flip Moment"),
            ("recovery_ccc",        "Recovery & CCC"),
            ("rebuild",             "The Rebuild"),
            ("book_vision",         "Book / Doc Vision"),
        ]
        buttons = [
            [{"text": label, "callback_data": f"bio_set:{entry_id}:{tag}"}]
            for tag, label in sections
        ]
        await send_telegram(chat_id, "Which section should this go in?",
                            reply_markup={"inline_keyboard": buttons})

    elif data.startswith("bio_set:"):
        _, entry_id, new_section = data.split(":", 2)
        async with db_pool.acquire() as conn:
            await conn.execute(
                "UPDATE biography_entries SET section=$1, verified=true, updated_at=NOW() WHERE id=$2",
                new_section, entry_id,
            )
        label = SECTION_LABELS.get(new_section, new_section)
        await send_telegram(chat_id, f"✅ Moved to <b>{label}</b> and confirmed.")

class BiographyCompileResponse(BaseModel):
    entry_count: int
    sections: dict
    markdown: str

@router.get("/biography/entries")
async def get_biography_entries(section: Optional[str] = None):
    from main import db_pool
    query  = "SELECT * FROM biography_entries"
    params = []
    if section:
        query  += " WHERE section=$1"
        params  = [section]
    query += " ORDER BY section ASC, created_at ASC"
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [dict(r) for r in rows]

@router.post("/biography/compile")
async def compile_biography():
    from main import db_pool
    async with db_pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM biography_entries ORDER BY section ASC, created_at ASC"
        )
    sections: dict = {}
    for row in rows:
        s = row["section"]
        sections.setdefault(s, []).append(dict(row))
    now   = datetime.now(timezone.utc).strftime("%B %d, %Y")
    count = len(rows)
    md_lines = [
        f"# KB_70 — IOSIF SKOROHODOV: BIOGRAPHICAL RECORD",
        f"**Compiled:** {now}  |  **Total entries:** {count}",
        f"**Classification:** CONFIDENTIAL — Founder Biography",
        "", "---", "",
    ]
    section_order = [
        "early_life", "family_dynamics", "pattern_recognition",
        "homeless_decision", "homeless_project", "homeless_timeline",
        "flip_moment", "recovery_ccc", "rebuild", "book_vision", "uncategorized",
    ]
    for sec in section_order:
        entries = sections.get(sec, [])
        if not entries:
            continue
        label = SECTION_LABELS.get(sec, sec)
        md_lines.append(f"## {label}")
        md_lines.append("")
        for e in entries:
            date_str = f" *({e['date_context']})*" if e.get("date_context") else ""
            weight   = e.get("emotional_weight", "medium")
            verified = "✓" if e.get("verified") else "~"
            md_lines.append(f"### [{verified}] {e['summary']}{date_str}")
            md_lines.append("")
            md_lines.append(f"> {e['raw_text']}")
            md_lines.append("")
            md_lines.append(f"*Weight: {weight} | Captured: {e['created_at'].strftime('%Y-%m-%d') if e.get('created_at') else 'unknown'}*")
            md_lines.append("")
    return BiographyCompileResponse(
        entry_count=count,
        sections={k: len(v) for k, v in sections.items()},
        markdown="\n".join(md_lines),
    )
