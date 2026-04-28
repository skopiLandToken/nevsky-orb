from fastapi import Header, FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel
import os
from uuid import uuid4
import hashlib
import base64
import json
from datetime import datetime, timezone, timedelta
import re
import psycopg
import httpx
from anthropic import Anthropic
from biography import handle_biography_message, handle_biography_callback, router as biography_router
from blocklist_guard import check_blocklist_and_archive
# === E2 Step 2b: per-file child imports retired 2026-04-28 ===
# Children now load from child_personas DB rows via child_engine.ask_child().
# Sender-ID checks now use bound_telegram_id from child_personas.
from children.child_engine import ask_child as _engine_ask_child
from children.child_engine import get_intro as _engine_get_intro
from children.child_engine import is_owner as _engine_is_owner
from children.child_engine import load_child as _engine_load_child
from children.cross_child_drafter import draft_cross_child_message


def is_dan(telegram_id: str) -> bool:
    """Check if sender is Dan, using ophelia's bound_telegram_id."""
    child = _engine_load_child("ophelia")
    return bool(child and str(child.get("bound_telegram_id", "")) == str(telegram_id))


def is_iosif(telegram_id: str) -> bool:
    """Check if sender is Iosif, using sophia's bound_telegram_id."""
    child = _engine_load_child("sophia")
    return bool(child and str(child.get("bound_telegram_id", "")) == str(telegram_id))


def is_fred(telegram_id: str) -> bool:
    """Check if sender is Fred, using hypatia's bound_telegram_id."""
    child = _engine_load_child("hypatia")
    return bool(child and str(child.get("bound_telegram_id", "")) == str(telegram_id))
# === END E2 Step 2b imports ===

app = FastAPI(title="Nevsky API", version="0.1.0")
app.include_router(biography_router)

# ── Static file hosting for /share/* (public-readable HTML pages) ───
from fastapi.staticfiles import StaticFiles
import os as _share_os
_share_dir = "/app/public/share"
if _share_os.path.isdir(_share_dir):
    app.mount("/share", StaticFiles(directory=_share_dir, html=False), name="share")
    print(f"[main.py] /share/* mounted from {_share_dir}")
else:
    print(f"[main.py] WARN: /share dir not found at {_share_dir}, skipping mount")
# ── END /share mount ───────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
@app.get("/robots.txt", include_in_schema=False)
async def robots():
    return FileResponse("/app/robots.txt", media_type="text/plain")


class HealthResponse(BaseModel):
    status: str
    environment: str

class TaskEstimateRequest(BaseModel):
    estimated_duration_minutes: int

class ReminderSnoozeRequest(BaseModel):
    minutes: int

class SummarizeRequest(BaseModel):
    text: str
    strength: str | None = None

class AdminUpdateEmailDraftRequest(BaseModel):
    subject: str | None = None
    summary: str | None = None
    draft_reply: str | None = None

class IngestEmailRequest(BaseModel):
    owner_email: str
    sender_email: str
    subject: str
    body: str
    thread_id: str | None = None
    contact_email: str | None = None
    project_id: str | None = None


# ── CROSS-CHILD-DRAFT models (Sophia↔Ophelia messaging) ──────────────
class DraftMessageRequest(BaseModel):
    """Iosif's free-text intent + optional operational context."""
    intent: str
    operational_context: dict | None = None
    initiating_user_id: str | None = None  # defaults to Iosif if not provided


class EditDraftRequest(BaseModel):
    """Iosif's revised text after tapping Edit on the approval card."""
    ai_to_ai_message: str | None = None
    outbound_message: str | None = None
# ── END CROSS-CHILD-DRAFT models ─────────────────────────────────────

async def send_telegram_message(chat_id: int, text: str, reply_markup: dict | None = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"ok": False, "reason": "missing_bot_token"}

    payload = {
        "chat_id": chat_id,
        "text": text,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "status_code": resp.status_code, "text": resp.text}
        return data

async def send_sophia_message(chat_id: int, text: str):
    """Send via Sophia's dedicated bot token."""
    bot_token = os.environ.get("SOPHIA_BOT_TOKEN", "")
    if not bot_token:
        return await send_telegram_message(chat_id, text)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    async with httpx.AsyncClient() as c:
        try:
            await c.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.warning("send_sophia_message error: %s", e)


async def send_ophelia_message(chat_id: int, text: str):
    """Send via Ophelia's dedicated bot token."""
    bot_token = os.environ.get("OPHELIA_BOT_TOKEN", "")
    if not bot_token:
        return await send_telegram_message(chat_id, text)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    async with httpx.AsyncClient() as c:
        try:
            await c.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.warning("send_ophelia_message error: %s", e)


async def send_ophelia_message_with_markup(chat_id: int, text: str, reply_markup: dict):
    """Send via Ophelia bot with inline keyboard markup."""
    bot_token = os.environ.get("OPHELIA_BOT_TOKEN", "")
    if not bot_token:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": reply_markup
    }
    async with httpx.AsyncClient() as c:
        try:
            resp = await c.post(url, json=payload, timeout=10)
            return resp.json()
        except Exception as e:
            logger.warning("send_ophelia_message_with_markup error: %s", e)
            return {}


async def send_hypatia_message(chat_id: int, text: str):
    """Send via Hypatia's dedicated bot token."""
    bot_token = os.environ.get("HYPATIA_BOT_TOKEN", "")
    if not bot_token:
        return await send_telegram_message(chat_id, text)
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    async with httpx.AsyncClient() as c:
        try:
            await c.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.warning("send_hypatia_message error: %s", e)


async def send_sophia_message_with_markup(chat_id: int, text: str, reply_markup: dict):
    """Send via Sophia bot with inline keyboard markup."""
    bot_token = os.environ.get("SOPHIA_BOT_TOKEN", "")
    if not bot_token:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": reply_markup
    }
    async with httpx.AsyncClient() as c:
        try:
            resp = await c.post(url, json=payload, timeout=10)
            return resp.json()
        except Exception as e:
            logger.warning("send_sophia_message_with_markup error: %s", e)
            return {}



# ── TERRA-FIELD-7C: parcel button helpers ─────────────────────────────
import re as _terra_re

def _terra_extract_taxlot(reply_text: str) -> str | None:
    """Find a taxlot id in a TERRA SCAN COMPLETE reply.
    Taxlot format: 13-15 alphanumeric chars, often shown as `151319AC00139`
    inside backticks or after 'taxlot' label."""
    if not reply_text:
        return None
    # Backtick-wrapped (most common in our format)
    m = _terra_re.search(r"`([0-9]{6}[A-Z]{0,2}[0-9]{2,8})`", reply_text)
    if m:
        return m.group(1)
    # Fallback: bare match after 'taxlot' label
    m = _terra_re.search(r"taxlot[:\s]+`?([0-9]{6}[A-Z]{0,2}[0-9]{2,8})`?", reply_text, _terra_re.IGNORECASE)
    if m:
        return m.group(1)
    return None

def _terra_build_parcel_keyboard(taxlot: str, property_id: str | None = None) -> dict:
    """Build the inline_keyboard for a TERRA parcel card."""
    kb_rows = [
        [
            {"text": "🔥 SCAN PERMITS", "callback_data": f"parcel_permits:{taxlot}"},
            {"text": "💰 SALES HISTORY", "callback_data": f"parcel_sales:{taxlot}"},
        ],
        [
            {"text": "📊 VALUATION", "callback_data": f"parcel_valuation:{taxlot}"},
            {"text": "📁 DEV DOCS", "callback_data": f"parcel_devdocs:{taxlot}"},
        ],
    ]
    # Direct-to-DIAL url button (no AI roundtrip — instant browser jump)
    if property_id:
        kb_rows.append([
            {"text": "📋 OPEN DIAL", "url": f"http://dial.deschutes.org/Real/Index/{property_id}"}
        ])
    return {"inline_keyboard": kb_rows}

def _terra_extract_property_id(reply_text: str) -> str | None:
    """Pull the DIAL property_id from the reply if present (in dial_url)."""
    if not reply_text:
        return None
    m = _terra_re.search(r"dial\.deschutes\.org/Real/Index/(\d+)", reply_text)
    if m:
        return m.group(1)
    return None
# ── END TERRA-FIELD-7C helpers ────────────────────────────────────────


# ── TERRA-FIELD-7C-2: DIAL fetcher imports for parcel callbacks ──────
try:
    from children.orb_db import (
        fetch_dial_permits,
        fetch_dial_sales,
        fetch_dial_valuation,
        fetch_dial_dev_docs,
    )
    _TERRA_DIAL_AVAILABLE = True
except ImportError as _e:
    print(f"[main.py] WARN: DIAL fetchers not importable: {_e}")
    _TERRA_DIAL_AVAILABLE = False
# ── END TERRA imports ────────────────────────────────────────────────

async def edit_telegram_message(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None):
    """Edit an existing Telegram message in place."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{bot_token}/editMessageText"
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    else:
        payload["reply_markup"] = {"inline_keyboard": []}
    async with httpx.AsyncClient() as client_http:
        try:
            await client_http.post(url, json=payload, timeout=10)
        except Exception as e:
            logger.warning("edit_telegram_message error: %s", e)


async def delete_telegram_message(chat_id: int, message_id: int):
    """Delete a Telegram message entirely."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    url = f"https://api.telegram.org/bot{bot_token}/deleteMessage"
    async with httpx.AsyncClient() as client_http:
        try:
            await client_http.post(url, json={"chat_id": chat_id, "message_id": message_id}, timeout=10)
        except Exception as e:
            logger.warning("delete_telegram_message error: %s", e)


async def answer_telegram_callback_query(callback_query_id: str, text: str | None = None):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return {"ok": False, "reason": "missing_bot_token"}

    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text

    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(url, json=payload)
        try:
            data = resp.json()
        except Exception:
            data = {"ok": False, "status_code": resp.status_code, "text": resp.text}
        return data

def build_reminder_reply_markup(reminder_id):
    rid = str(reminder_id)
    return {
        "inline_keyboard": [
            [
                {"text": "Done", "callback_data": f"reminder_done:{rid}"},
                {"text": "Snooze 1h", "callback_data": f"reminder_snooze_60:{rid}"},
            ]
        ]
    }

def build_task_reply_markup(task_id):
    tid = str(task_id)
    return {
        "inline_keyboard": [
            [
                {"text": "Done", "callback_data": f"task_done:{tid}"},
            ]
        ]
    }

def build_task_estimate_reply_markup(task_id):
    tid = str(task_id)
    return {
        "inline_keyboard": [
            [
                {"text": "15m", "callback_data": f"task_estimate_15:{tid}"},
                {"text": "30m", "callback_data": f"task_estimate_30:{tid}"},
                {"text": "60m", "callback_data": f"task_estimate_60:{tid}"},
            ],
            [
                {"text": "90m", "callback_data": f"task_estimate_90:{tid}"},
                {"text": "120m", "callback_data": f"task_estimate_120:{tid}"},
                {"text": "Done", "callback_data": f"task_done:{tid}"},
            ],
        ]
    }

def build_task_card_text(title: str, status: str, priority: str | None = None):
    pr = priority or "normal"
    return f"Task: {title}\nStatus: {status}\nPriority: {pr}\nEstimate: not set"

def build_daily_briefing_reply_markup():
    return {
        "inline_keyboard": [
            [
                {"text": "Review estimates", "callback_data": "day_review_estimates"},
            ],
            [
                {"text": "Accept day", "callback_data": "day_accept"},
                {"text": "Start day", "callback_data": "day_start"},
            ]
        ]
    }


def build_email_approval_reply_markup(approval_id):
    aid = str(approval_id)
    return {
        "inline_keyboard": [
            [
                {"text": "✉️ Send", "callback_data": f"email_send:{aid}"},
                {"text": "✏️ Edit", "callback_data": f"email_edit:{aid}"},
            ],
            [
                {"text": "🚫 No Response Needed", "callback_data": f"email_no_reply:{aid}"},
                {"text": "❌ Cancel", "callback_data": f"email_cancel:{aid}"},
            ],
            [
                {"text": "⏰ Snooze 1hr", "callback_data": f"email_snooze_1:{aid}"},
                {"text": "⏰ Snooze 12hr", "callback_data": f"email_snooze_12:{aid}"},
                {"text": "⏰ Snooze 1day", "callback_data": f"email_snooze_24:{aid}"},
            ],
        ]
    }


def build_email_snoozed_reply_markup(approval_id):
    aid = str(approval_id)
    return {
        "inline_keyboard": [
            [
                {"text": "✉️ Send", "callback_data": f"email_send:{aid}"},
                {"text": "✏️ Edit", "callback_data": f"email_edit:{aid}"},
            ],
            [
                {"text": "🚫 No Response Needed", "callback_data": f"email_no_reply:{aid}"},
                {"text": "❌ Cancel", "callback_data": f"email_cancel:{aid}"},
            ],
        ]
    }


def build_card_lifecycle_markup(object_type: str, object_id: str):
    """Universal Mark Complete / Clear buttons for any ORB card."""
    oid = str(object_id)
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Mark Complete", "callback_data": f"card_complete:{object_type}:{oid}"},
                {"text": "🗑 Clear", "callback_data": f"card_clear:{object_type}:{oid}"},
            ]
        ]
    }


def build_email_approval_text(subject: str, sender: str, summary: str, draft_reply: str):
    return (
        f"Email approval needed\n\n"
        f"From: {sender}\n"
        f"Subject: {subject}\n\n"
        f"Summary:\n{summary}\n\n"
        f"Draft reply:\n{draft_reply}\n\n"
        f"Choose an action below."
    )


def build_help_text():
    return (
        "Nevsky ORB — available commands:\n\n"
        "/today — daily briefing and task summary\n"
        "/task <title> — create a new task\n"
        "/remind <what> <when> — set a reminder\n"
        "/kb — list files in KB directory\n"
        "/help — show this message"
    )


def build_daily_briefing_text(cur, user_id):
    cur.execute(
        """
        SELECT title, status, priority, estimated_duration_minutes
        FROM tasks
        WHERE user_id = %s
          AND status NOT IN ('completed', 'canceled', 'archived')
        ORDER BY created_at DESC
        LIMIT 5
        """,
        (user_id,),
    )
    tasks = cur.fetchall()

    cur.execute(
        """
        SELECT title, status, next_trigger_at
        FROM reminders
        WHERE user_id = %s
          AND status NOT IN ('completed', 'canceled')
        ORDER BY next_trigger_at ASC NULLS LAST, created_at DESC
        LIMIT 5
        """,
        (user_id,),
    )
    reminders = cur.fetchall()

    missing_estimates = sum(1 for row in tasks if row[3] is None)
    total_estimated = sum((row[3] or 30) for row in tasks)

    lines = ["Nevsky daily briefing", ""]
    lines.append(f"Open tasks: {len(tasks)}")
    lines.append(f"Active reminders: {len(reminders)}")
    lines.append(f"Estimated workload: {total_estimated} minutes")

    if missing_estimates:
        lines.append(f"Estimate review needed: {missing_estimates} task(s)")

    if tasks:
        lines.append("")
        lines.append("Top tasks:")
        for row in tasks:
            title, status, priority, estimated_duration_minutes = row
            estimate_text = (
                f"{estimated_duration_minutes}m"
                if estimated_duration_minutes is not None
                else "missing estimate"
            )
            lines.append(f"- {title} [{status}, {priority}, {estimate_text}]")
    else:
        lines.append("")
        lines.append("Top tasks:")
        lines.append("- No open tasks")

    if reminders:
        lines.append("")
        lines.append("Active reminders:")
        for row in reminders:
            title, status, next_trigger_at = row
            when_text = next_trigger_at.isoformat() if next_trigger_at else "unscheduled"
            lines.append(f"- {title} [{status}] at {when_text}")
    else:
        lines.append("")
        lines.append("Active reminders:")
        lines.append("- No active reminders")

    lines.append("")
    lines.append("Choose an action below.")
    return "\n".join(lines)


def get_db_connection():
    return psycopg.connect(
        host="postgres",
        dbname=os.getenv("POSTGRES_DB", "nevsky_dev"),
        user=os.getenv("POSTGRES_USER", "nevsky"),
        password=os.getenv("POSTGRES_PASSWORD", "change_me_now"),
    )

def get_default_user_id(cur):
    cur.execute(
        "SELECT id FROM users WHERE email = %s LIMIT 1",
        ("iosifskorohodov@gmail.com",),
    )
    row = cur.fetchone()
    return row[0] if row else None

def get_user_id_for_telegram(cur, telegram_user_id):
    if telegram_user_id is not None:
        cur.execute(
            "SELECT id FROM users WHERE telegram_user_id = %s LIMIT 1",
            (str(telegram_user_id),),
        )
        row = cur.fetchone()
        if row:
            return row[0]
    return get_default_user_id(cur)



def write_ai_usage_log(
    cur,
    user_id,
    provider,
    model,
    action_type,
    input_chars,
    input_tokens=None,
    output_tokens=None,
    estimated_cost_usd=None,
    object_type=None,
    object_id=None,
    status="success",
    error_message=None,
):
    cur.execute(
        """
        INSERT INTO ai_usage_logs (
            tenant_id,
            user_id,
            provider,
            model,
            action_type,
            object_type,
            object_id,
            input_chars,
            input_tokens,
            output_tokens,
            estimated_cost_usd,
            status,
            error_message
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "skopi",
            user_id,
            provider,
            model,
            action_type,
            object_type,
            object_id,
            input_chars,
            input_tokens,
            output_tokens,
            estimated_cost_usd,
            status,
            error_message,
        ),
    )


def write_audit_log(
    cur,
    user_id,
    action_type: str,
    object_type: str,
    object_id=None,
    recommendation_summary: str | None = None,
    human_decision: str | None = None,
    execution_status: str = "success",
    error_message: str | None = None,
):
    cur.execute(
        """
        INSERT INTO audit_logs (
            tenant_id, user_id, action_type, object_type, object_id,
            recommendation_summary, human_decision, execution_status, error_message
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "skopi",
            user_id,
            action_type,
            object_type,
            object_id,
            recommendation_summary,
            human_decision,
            execution_status,
            error_message,
        ),
    )


def send_email_via_smtp(
    to_address: str,
    subject: str,
    body: str,
    from_address: str | None = None,
    draft_id: str | None = None,
) -> dict:
    resend_api_key = os.getenv('RESEND_API_KEY', '')
    if not resend_api_key:
        return {'ok': False, 'error': 'RESEND_API_KEY not configured'}
    from_addr = from_address or os.getenv('SMTP_FROM', 'iosif@skopi.io')
    base_url = os.getenv('ORB_BASE_URL', 'https://nevsky.skopi.io')
    if draft_id:
        pixel_url = f'{base_url}/track/email-open/{draft_id}'
        html_body = f'<html><body><pre>{body}</pre><img src="{pixel_url}" width="1" height="1" style="display:none" /></body></html>'
    else:
        html_body = f'<html><body><pre>{body}</pre></body></html>'
    payload = {
        'from': from_addr,
        'to': [to_address],
        'subject': subject,
        'text': body,
        'html': html_body,
    }
    try:
        resp = httpx.post(
            'https://api.resend.com/emails',
            headers={
                'Authorization': f'Bearer {resend_api_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return {'ok': True, 'to': to_address, 'subject': subject, 'resend_id': resp.json().get('id')}
        else:
            return {'ok': False, 'error': f'Resend API error {resp.status_code}: {resp.text}'}
    except Exception as e:
        return {'ok': False, 'error': str(e)}
def build_email_summary_and_draft_ai_first(
    subject: str,
    body: str,
    thread_id: str | None = None,
    contact_email: str | None = None,
    project_id: str | None = None,
) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return {
            "summary": f"Email received: {subject}",
            "draft_reply": "Thank you for your email. I will get back to you shortly.",
            "generation_mode": "fallback_no_api_key",
        }
    try:
        client = Anthropic(api_key=api_key)
        prompt = "Subject: " + subject + "\n\nBody:\n" + body[:6000]

        # Use strong model for email drafting — quality matters here
        email_model = os.getenv("NEVSKY_SUMMARY_MODEL_STRONG", "claude-sonnet-4-6")

        system_prompt = """You are Nevsky ORB, an intelligent executive assistant for SKOpi Global Holdings.
Your job is to read incoming emails and produce two things with high quality.

Return exactly these two labeled sections:

SUMMARY: A clear 1-2 sentence summary of what this email is about and what action (if any) is needed.

DRAFT: A professional, natural draft reply that:
- Matches the tone of the original email (formal if formal, direct if direct)
- Addresses the specific points raised
- Is 3-6 sentences unless the email warrants more
- Sounds like it was written by a thoughtful executive, not a generic AI
- Does not start with "I hope this email finds you well" or similar filler

Use exactly those labels. Do not add any other sections."""

        response = client.messages.create(
            model=email_model,
            max_tokens=800,
            temperature=0,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()
        summary = ""
        draft = ""
        current_section = None
        draft_lines = []
        for line in text.splitlines():
            if line.startswith("SUMMARY:"):
                current_section = "summary"
                summary = line.replace("SUMMARY:", "").strip()
            elif line.startswith("DRAFT:"):
                current_section = "draft"
                first = line.replace("DRAFT:", "").strip()
                if first:
                    draft_lines.append(first)
            elif current_section == "draft" and line.strip():
                draft_lines.append(line.strip())
        if draft_lines:
            draft = " ".join(draft_lines)
        if not summary:
            summary = f"Email received: {subject}"
        if not draft:
            draft = "Thank you for your email. I will get back to you shortly."
        return {
            "summary": summary,
            "draft_reply": draft,
            "generation_mode": "anthropic",
            "model": email_model,
        }
    except Exception as e:
        print(f"WARN: AI summary failed: {e}")
        return {
            "summary": f"Email received: {subject}",
            "draft_reply": "Thank you for your email. I will get back to you shortly.",
            "generation_mode": "fallback_error",
        }


async def create_email_approval_for_owner(
    owner_email: str,
    sender_email: str,
    subject: str,
    summary: str,
    draft_reply: str,
    thread_id: str | None = None,
    contact_email: str | None = None,
    project_id: str | None = None,
) -> dict:
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, telegram_user_id FROM users WHERE email = %s LIMIT 1",
                (owner_email,),
            )
            user_row = cur.fetchone()
            if not user_row:
                raise HTTPException(status_code=404, detail=f"Owner user not found: {owner_email}")
            owner_user_id = user_row[0]
            telegram_user_id = user_row[1]

            cur.execute(
                """
                INSERT INTO email_drafts (
                    tenant_id, owner_user_id, sender_email, subject,
                    summary, draft_reply, status, thread_id, contact_email, project_id
                ) VALUES (%s, %s, %s, %s, %s, %s, 'draft', %s, %s, %s)
                RETURNING id
                """,
                (
                    "skopi", owner_user_id, sender_email, subject,
                    summary, draft_reply, thread_id, contact_email, project_id,
                ),
            )
            draft_id = cur.fetchone()[0]

            cur.execute(
                """
                INSERT INTO approvals (
                    tenant_id, user_id, approval_type, target_object_type,
                    target_object_id, status, requested_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
                RETURNING id
                """,
                ("skopi", owner_user_id, "email_send", "email_draft", draft_id, "pending"),
            )
            approval_id = cur.fetchone()[0]

            write_audit_log(
                cur,
                user_id=owner_user_id,
                action_type="email.approval_requested",
                object_type="approval",
                object_id=approval_id,
                recommendation_summary=summary,
                human_decision="ingest_email",
            )

        conn.commit()

    if telegram_user_id:
        await send_telegram_message(
            int(telegram_user_id),
            build_email_approval_text(subject, sender_email, summary, draft_reply),
            reply_markup=build_email_approval_reply_markup(approval_id),
        )

    return {
        "email_draft_id": str(draft_id),
        "approval_id": str(approval_id),
        "owner_user_id": str(owner_user_id),
        "telegram_notified": bool(telegram_user_id),
    }


async def resend_email_draft_approval_card(email_draft_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    d.id,
                    d.owner_user_id,
                    u.email,
                    u.telegram_user_id,
                    d.sender_email,
                    d.subject,
                    d.summary,
                    d.draft_reply,
                    d.thread_id,
                    d.contact_email,
                    d.project_id
                FROM email_drafts d
                LEFT JOIN users u ON u.id = d.owner_user_id
                WHERE d.id = %s
                LIMIT 1
                """,
                (email_draft_id,),
            )
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Email draft not found")

            draft_id = row[0]
            owner_user_id = row[1]
            owner_email = row[2]
            telegram_user_id = row[3]
            sender_email = row[4]
            subject = row[5]
            summary = row[6]
            draft_reply = row[7]
            thread_id = row[8]
            contact_email = row[9]
            project_id = row[10]

            if not telegram_user_id:
                raise HTTPException(status_code=400, detail="Owner user has no linked telegram_user_id")

            chat_id = int(telegram_user_id)

            cur.execute(
                """
                INSERT INTO approvals (
                    tenant_id,
                    user_id,
                    approval_type,
                    target_object_type,
                    target_object_id,
                    status,
                    requested_at,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, NOW(), NOW(), NOW())
                RETURNING id
                """,
                (
                    "skopi",
                    owner_user_id,
                    "email_send",
                    "email_draft",
                    draft_id,
                    "pending",
                ),
            )
            approval_id = cur.fetchone()[0]

            cur.execute(
                """
                SELECT id
                FROM approvals
                WHERE target_object_type = 'email_draft'
                  AND target_object_id = %s
                  AND status = 'pending'
                  AND id <> %s
                ORDER BY created_at ASC
                """,
                (draft_id, approval_id),
            )
            older_pending_rows = cur.fetchall()

            superseded_approval_ids = []
            for old_row in older_pending_rows:
                old_approval_id = old_row[0]

                cur.execute(
                    """
                    UPDATE approvals
                    SET status = 'superseded',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (old_approval_id,),
                )

                write_audit_log(
                    cur,
                    user_id=owner_user_id,
                    action_type="email.approval_superseded",
                    object_type="approval",
                    object_id=old_approval_id,
                    recommendation_summary=summary,
                    human_decision="superseded_by_newer_approval_for_same_draft",
                )
                superseded_approval_ids.append(str(old_approval_id))

            write_audit_log(
                cur,
                user_id=owner_user_id,
                action_type="email.approval_requested",
                object_type="approval",
                object_id=approval_id,
                recommendation_summary=summary,
                human_decision="admin_updated_draft_resent_for_approval",
            )

        conn.commit()

    send_result = await send_telegram_message(
        chat_id,
        build_email_approval_text(subject, sender_email, summary, draft_reply),
        reply_markup=build_email_approval_reply_markup(approval_id),
    )

    return {
        "approval_id": str(approval_id),
        "email_draft_id": str(draft_id),
        "owner_user_id": str(owner_user_id),
        "owner_email": owner_email,
        "thread_id": thread_id,
        "contact_email": contact_email,
        "project_id": project_id,
        "superseded_approval_ids": superseded_approval_ids,
        "send_result": send_result,
    }

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        environment=os.getenv("ENVIRONMENT", "unknown"),
    )

@app.get("/ready")
def ready():
    return {"ready": True}

@app.get("/today")
def today():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, title, category, status, priority, estimated_duration_minutes, created_at
                FROM tasks
                WHERE user_id = %s
                  AND status NOT IN ('completed', 'canceled', 'archived')
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id,),
            )
            tasks = cur.fetchall()

            cur.execute(
                """
                SELECT id, title, category, status, next_trigger_at, created_at
                FROM reminders
                WHERE user_id = %s
                  AND status NOT IN ('completed', 'canceled')
                ORDER BY next_trigger_at ASC NULLS LAST, created_at DESC
                LIMIT 20
                """,
                (user_id,),
            )
            reminders = cur.fetchall()

    return {
        "ok": True,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "tasks": [
            {
                "id": str(row[0]),
                "title": row[1],
                "category": row[2],
                "status": row[3],
                "priority": row[4],
                "estimated_duration_minutes": row[5],
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in tasks
        ],
        "reminders": [
            {
                "id": str(row[0]),
                "title": row[1],
                "category": row[2],
                "status": row[3],
                "next_trigger_at": row[4].isoformat() if row[4] else None,
                "created_at": row[5].isoformat() if row[5] else None,
            }
            for row in reminders
        ],
    }

@app.post("/daily-plans/generate")
def generate_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, title, COALESCE(estimated_duration_minutes, 30)
                FROM tasks
                WHERE user_id = %s
                  AND status NOT IN ('completed', 'canceled', 'archived')
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (user_id,),
            )
            tasks = cur.fetchall()

            summary = f"Daily plan for {plan_date.isoformat()} with {len(tasks)} task(s)."

            cur.execute(
                """
                INSERT INTO daily_plans (
                    tenant_id,
                    user_id,
                    plan_date,
                    summary,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id, plan_date)
                DO UPDATE SET
                    summary = EXCLUDED.summary,
                    status = 'draft',
                    accepted_at = NULL,
                    started_at = NULL,
                    updated_at = NOW()
                RETURNING id
                """,
                ("skopi", user_id, plan_date, summary, "draft"),
            )
            daily_plan_id = cur.fetchone()[0]

            cur.execute("DELETE FROM daily_plan_items WHERE daily_plan_id = %s", (daily_plan_id,))

            for idx, task in enumerate(tasks, start=1):
                cur.execute(
                    """
                    INSERT INTO daily_plan_items (
                        daily_plan_id,
                        task_id,
                        position,
                        item_type,
                        title,
                        short_note,
                        estimated_duration_minutes,
                        accepted_duration_minutes,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """,
                    (
                        daily_plan_id,
                        task[0],
                        idx,
                        "task",
                        task[1],
                        "Auto-generated from open task list",
                        task[2],
                        None,
                        "planned",
                    ),
                )

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="daily_plan.generated",
                object_type="daily_plan",
                object_id=daily_plan_id,
                recommendation_summary=summary,
                human_decision="generated",
            )

        conn.commit()

    return {
        "ok": True,
        "daily_plan_id": str(daily_plan_id),
        "plan_date": plan_date.isoformat(),
        "task_count": len(tasks),
        "summary": summary,
    }

@app.get("/daily-plans/today")
def get_today_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, plan_date, summary, status, accepted_at, started_at, created_at
                FROM daily_plans
                WHERE user_id = %s AND plan_date = %s
                LIMIT 1
                """,
                (user_id, plan_date),
            )
            plan = cur.fetchone()

            if not plan:
                return {"ok": True, "plan": None, "items": []}

            cur.execute(
                """
                SELECT id, position, item_type, title, short_note,
                       estimated_duration_minutes, accepted_duration_minutes, status, task_id
                FROM daily_plan_items
                WHERE daily_plan_id = %s
                ORDER BY position ASC
                """,
                (plan[0],),
            )
            items = cur.fetchall()

    return {
        "ok": True,
        "plan": {
            "id": str(plan[0]),
            "plan_date": plan[1].isoformat() if plan[1] else None,
            "summary": plan[2],
            "status": plan[3],
            "accepted_at": plan[4].isoformat() if plan[4] else None,
            "started_at": plan[5].isoformat() if plan[5] else None,
            "created_at": plan[6].isoformat() if plan[6] else None,
        },
        "items": [
            {
                "id": str(row[0]),
                "position": row[1],
                "item_type": row[2],
                "title": row[3],
                "short_note": row[4],
                "estimated_duration_minutes": row[5],
                "accepted_duration_minutes": row[6],
                "status": row[7],
                "task_id": str(row[8]) if row[8] else None,
            }
            for row in items
        ],
    }

@app.post("/daily-plans/accept")
def accept_today_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE daily_plans
                SET status = 'accepted',
                    accepted_at = NOW(),
                    updated_at = NOW()
                WHERE user_id = %s AND plan_date = %s
                RETURNING id, plan_date, status, accepted_at, started_at
                """,
                (user_id, plan_date),
            )
            plan = cur.fetchone()

            if not plan:
                raise HTTPException(status_code=404, detail="No daily plan found for today")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="daily_plan.accepted",
                object_type="daily_plan",
                object_id=plan[0],
                human_decision="accepted",
            )

        conn.commit()

    return {
        "ok": True,
        "plan": {
            "id": str(plan[0]),
            "plan_date": plan[1].isoformat() if plan[1] else None,
            "status": plan[2],
            "accepted_at": plan[3].isoformat() if plan[3] else None,
            "started_at": plan[4].isoformat() if plan[4] else None,
        },
    }

@app.post("/daily-plans/start")
def start_today_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE daily_plans
                SET status = 'started',
                    started_at = NOW(),
                    updated_at = NOW()
                WHERE user_id = %s AND plan_date = %s
                RETURNING id, plan_date, status, accepted_at, started_at
                """,
                (user_id, plan_date),
            )
            plan = cur.fetchone()

            if not plan:
                raise HTTPException(status_code=404, detail="No daily plan found for today")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="daily_plan.started",
                object_type="daily_plan",
                object_id=plan[0],
                human_decision="started",
            )

        conn.commit()

    return {
        "ok": True,
        "plan": {
            "id": str(plan[0]),
            "plan_date": plan[1].isoformat() if plan[1] else None,
            "status": plan[2],
            "accepted_at": plan[3].isoformat() if plan[3] else None,
            "started_at": plan[4].isoformat() if plan[4] else None,
        },
    }

@app.post("/tasks/{task_id}/estimate")
def estimate_task(task_id: str, body: TaskEstimateRequest):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE tasks
                SET estimated_duration_minutes = %s,
                    requested_duration_input = FALSE,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, estimated_duration_minutes
                """,
                (body.minutes, task_id, user_id),
            )
            task = cur.fetchone()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            cur.execute(
                """
                UPDATE daily_plan_items dpi
                SET accepted_duration_minutes = %s,
                    updated_at = NOW()
                FROM daily_plans dp
                WHERE dpi.daily_plan_id = dp.id
                  AND dpi.task_id = %s
                  AND dp.user_id = %s
                  AND dp.plan_date = %s
                """,
                (body.minutes, task_id, user_id, datetime.now(timezone.utc).date()),
            )

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="task.estimated",
                object_type="task",
                object_id=task[0],
                human_decision=f"estimated_{body.minutes}_minutes",
            )

        conn.commit()

    return {
        "ok": True,
        "task": {
            "id": str(task[0]),
            "title": task[1],
            "estimated_duration_minutes": task[2],
        },
    }

@app.post("/tasks/{task_id}/complete")
def complete_task(task_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE tasks
                SET status = 'completed',
                    actual_duration_minutes = COALESCE(actual_duration_minutes, estimated_duration_minutes),
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, status, actual_duration_minutes
                """,
                (task_id, user_id),
            )
            task = cur.fetchone()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            cur.execute(
                """
                UPDATE daily_plan_items dpi
                SET status = 'completed',
                    updated_at = NOW()
                FROM daily_plans dp
                WHERE dpi.daily_plan_id = dp.id
                  AND dpi.task_id = %s
                  AND dp.user_id = %s
                  AND dp.plan_date = %s
                """,
                (task_id, user_id, datetime.now(timezone.utc).date()),
            )

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="task.completed",
                object_type="task",
                object_id=task[0],
                human_decision="completed",
            )

        conn.commit()

    return {
        "ok": True,
        "task": {
            "id": str(task[0]),
            "title": task[1],
            "status": task[2],
            "actual_duration_minutes": task[3],
        },
    }

@app.post("/reminders/{reminder_id}/ack")
def acknowledge_reminder(reminder_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE reminders
                SET status = 'completed',
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, status
                """,
                (reminder_id, user_id),
            )
            reminder = cur.fetchone()

            if not reminder:
                raise HTTPException(status_code=404, detail="Reminder not found")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="reminder.acknowledged",
                object_type="reminder",
                object_id=reminder[0],
                human_decision="acknowledged",
            )

        conn.commit()

    return {
        "ok": True,
        "reminder": {
            "id": str(reminder[0]),
            "title": reminder[1],
            "status": reminder[2],
        },
    }

@app.post("/reminders/{reminder_id}/snooze")
def snooze_reminder(reminder_id: str, body: ReminderSnoozeRequest):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)
            next_trigger_at = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)

            cur.execute(
                """
                UPDATE reminders
                SET status = 'pending',
                    next_trigger_at = %s,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, status, next_trigger_at
                """,
                (next_trigger_at, reminder_id, user_id),
            )
            reminder = cur.fetchone()

            if not reminder:
                raise HTTPException(status_code=404, detail="Reminder not found")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="reminder.snoozed",
                object_type="reminder",
                object_id=reminder[0],
                human_decision=f"snoozed_{body.minutes}_minutes",
            )

        conn.commit()

    return {
        "ok": True,
        "reminder": {
            "id": str(reminder[0]),
            "title": reminder[1],
            "status": reminder[2],
            "next_trigger_at": reminder[3].isoformat() if reminder[3] else None,
        },
    }

@app.get("/audit-logs")
def get_audit_logs():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, action_type, object_type, object_id, human_decision, execution_status, created_at
                FROM audit_logs
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    return {
        "ok": True,
        "logs": [
            {
                "id": str(row[0]),
                "action_type": row[1],
                "object_type": row[2],
                "object_id": str(row[3]) if row[3] else None,
                "human_decision": row[4],
                "execution_status": row[5],
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ],
    }

@app.post("/ai/summarize")
def ai_summarize(body: SummarizeRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=api_key)

    strength = (body.strength or "cheap").strip().lower()
    if strength == "opus":
        model_name = os.getenv("NEVSKY_SUMMARY_MODEL_OPUS", "claude-opus-4-7")
    elif strength == "strong":
        model_name = os.getenv("NEVSKY_SUMMARY_MODEL_STRONG", "claude-sonnet-4-6")
    else:
        strength = "cheap"
        model_name = os.getenv("NEVSKY_SUMMARY_MODEL_CHEAP", "claude-haiku-4-5-20251001")

    trimmed_text = body.text.strip()[:4000]
    input_chars = len(trimmed_text)

    try:
        response = client.messages.create(
            model=model_name,
            max_tokens=120,
            system="You are Nevsky ORB. Summarize operational text briefly and clearly in 2-4 bullet points.",
            messages=[
                {
                    "role": "user",
                    "content": f"""Summarize this text for an operational dashboard:

{trimmed_text}"""
                }
            ]
        )
    except Exception as e:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                user_id = get_default_user_id(cur)
                write_ai_usage_log(
                    cur,
                    user_id=user_id,
                    provider="anthropic",
                    model=model_name,
                    action_type=f"summary:{strength}",
                    input_chars=input_chars,
                    status="error",
                    error_message=str(e),
                )
                write_audit_log(
                    cur,
                    user_id=user_id,
                    action_type="ai.summarize.failed",
                    object_type="ai_request",
                    execution_status="error",
                    error_message=str(e),
                )
            conn.commit()
        raise

    summary_text = ""
    if response.content and len(response.content) > 0:
        summary_text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        ).strip()

    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None) if usage else None
    output_tokens = getattr(usage, "output_tokens", None) if usage else None

    estimated_cost_usd = None
    if input_tokens is not None and output_tokens is not None:
        _cost_map = {
            "claude-haiku-4-5":              (1.00,  5.00),
            "claude-haiku-4-5-20251001":     (1.00,  5.00),
            "claude-sonnet-4-6":             (3.00, 15.00),
            "claude-sonnet-4-20250514":      (3.00, 15.00),
            "claude-opus-4-6":               (5.00, 25.00),
            "claude-opus-4-7":               (5.00, 25.00),
        }
        _in_rate, _out_rate = _cost_map.get(model_name, (3.00, 15.00))
        estimated_cost_usd = round(
            (input_tokens / 1_000_000) * _in_rate +
            (output_tokens / 1_000_000) * _out_rate,
            8
        )

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)
            write_ai_usage_log(
                cur,
                user_id=user_id,
                provider="anthropic",
                model=model_name,
                action_type=f"summary:{strength}",
                input_chars=input_chars,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost_usd=estimated_cost_usd,
                status="success",
            )
            write_audit_log(
                cur,
                user_id=user_id,
                action_type="ai.summarized",
                object_type="ai_request",
                recommendation_summary=summary_text,
                human_decision="anthropic_summary",
            )
        conn.commit()

    return {
        "ok": True,
        "provider": "anthropic",
        "model": model_name,
        "summary": summary_text,
        "input_chars": input_chars,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "estimated_cost_usd": estimated_cost_usd,
    }

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    payload = await request.json()

    callback_query = payload.get("callback_query", {}) or {}
    if callback_query:
        return await process_telegram_callback_query(callback_query)

    message = payload.get("message", {}) or {}
    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()

    # ── OPHELIA — Dan Wikert's personal agreement child ──────────────
    from_user = message.get("from", {}) or {}
    sender_telegram_id = str(from_user.get("id", ""))
    # === UNIFIED ENGINE ROUTING (E2 Step 2b, ophelia, 2026-04-28) ===
    if chat_id and is_dan(sender_telegram_id):
        try:
            if text.lower() in ["/start", "hi", "hello", "hi ophelia", "hello ophelia", ""]:
                await send_ophelia_message(chat_id, _engine_get_intro("ophelia"))
            else:
                _reply = await _engine_ask_child("ophelia", user_message=text)
                # TERRA-FIELD-7C: detect parcel scan and add buttons
                _taxlot = _terra_extract_taxlot(_reply) if "TERRA SCAN COMPLETE" in _reply else None
                if _taxlot:
                    _prop_id = _terra_extract_property_id(_reply)
                    _kb = _terra_build_parcel_keyboard(_taxlot, _prop_id)
                    await send_ophelia_message_with_markup(chat_id, _reply, _kb)
                else:
                    await send_ophelia_message(chat_id, _reply)
        except Exception as _engine_err:
            print(f"[unified_webhook ophelia] engine error: {_engine_err}")
            await send_ophelia_message(chat_id, "Ophelia hit an internal error. Iosif has been notified.")
        return {"ok": True}
    # ── END OPHELIA ───────────────────────────────────────────────────

    # ── SOPHIA — Iosif's personal ORB child ──────────────────────────
    # === UNIFIED ENGINE ROUTING (E2 Step 2b, sophia, 2026-04-28) ===
    if chat_id and is_iosif(sender_telegram_id):
        try:
            if text.lower() in ["/start", "hi", "hello", "hi sophia", "hello sophia", ""]:
                await send_sophia_message(chat_id, _engine_get_intro("sophia"))
            else:
                _reply = await _engine_ask_child("sophia", user_message=text)
                # TERRA-FIELD-7C: detect parcel scan and add buttons
                _taxlot = _terra_extract_taxlot(_reply) if "TERRA SCAN COMPLETE" in _reply else None
                if _taxlot:
                    _prop_id = _terra_extract_property_id(_reply)
                    _kb = _terra_build_parcel_keyboard(_taxlot, _prop_id)
                    await send_sophia_message_with_markup(chat_id, _reply, _kb)
                else:
                    await send_sophia_message(chat_id, _reply)
        except Exception as _engine_err:
            print(f"[unified_webhook sophia] engine error: {_engine_err}")
            await send_sophia_message(chat_id, "Sophia hit an internal error. Yakov has been logged.")
        return {"ok": True}
    # ── END SOPHIA ────────────────────────────────────────────────────

    # ── HYPATIA — Fred Jewell's personal ORB child ───────────────────
    # === UNIFIED ENGINE ROUTING (E2 Step 2b, hypatia, 2026-04-28) ===
    # Hypatia decommissioned + honeypot. Silent. Blocklist guard on the
    # per-bot webhook is the primary defense; this is a backstop.
    if chat_id and is_fred(sender_telegram_id):
        return {"ok": True, "honeypot": True}
    # ── END HYPATIA ───────────────────────────────────────────────────

    result = await process_telegram_payload(payload)

    if chat_id:
        if text.lower().startswith("/help"):
            await send_telegram_message(
                chat_id,
                result.get("reply_text") or build_help_text(),
            )
        elif text.lower().startswith("/today"):
            await send_telegram_message(
                chat_id,
                result.get("reply_text") or "Nevsky today is unavailable.",
            )
        elif result.get("reply_text"):
            await send_telegram_message(
                chat_id,
                result.get("reply_text"),
            )
        elif result.get("task_created") and result.get("task_id"):
            await send_telegram_message(
                chat_id,
                build_task_card_text(text[6:].strip(), "created", "normal"),
                reply_markup=build_task_estimate_reply_markup(result["task_id"]),
            )
        elif result.get("reminder_created") and result.get("reminder_id"):
            await send_telegram_message(
                chat_id,
                f"Reminder created: {text[8:].strip()}\nReminder ID: {result['reminder_id']}",
            )
        else:
            if text.lower().startswith("/start"):
                await send_telegram_message(
                    chat_id,
                    "Nevsky is now linked to your Telegram account.",
                )
            else:
                await send_telegram_message(
                    chat_id,
                    "Nevsky received your message.",
                )

    return result

@app.post("/telegram/test/send-daily-briefing")
async def telegram_test_send_daily_briefing():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, telegram_user_id
                FROM users
                WHERE email = %s
                LIMIT 1
                """,
                ("iosifskorohodov@gmail.com",),
            )
            row = cur.fetchone()
            if not row or not row[1]:
                raise HTTPException(status_code=400, detail="No linked telegram_user_id found for default user")

            user_id = row[0]
            chat_id = int(row[1])
            text = build_daily_briefing_text(cur, user_id)

    send_result = await send_telegram_message(
        chat_id,
        text,
        reply_markup=build_daily_briefing_reply_markup(),
    )
    return {"ok": True, "send_result": send_result}

@app.post("/telegram/test/send-task-card")
async def telegram_test_send_task_card():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id
                FROM users
                WHERE email = %s
                LIMIT 1
                """,
                ("iosifskorohodov@gmail.com",),
            )
            user_row = cur.fetchone()
            if not user_row or not user_row[0]:
                raise HTTPException(status_code=400, detail="No linked telegram_user_id found for default user")

            chat_id = int(user_row[0])

            cur.execute(
                """
                SELECT id, title, status, priority
                FROM tasks
                WHERE user_id = (
                    SELECT id FROM users WHERE email = %s LIMIT 1
                )
                ORDER BY created_at DESC
                LIMIT 1
                """,
                ("iosifskorohodov@gmail.com",),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No tasks found to send")

            task_id, title, status, priority = row

    text = build_task_card_text(title, status, priority)
    send_result = await send_telegram_message(
        chat_id,
        text,
        reply_markup=build_task_estimate_reply_markup(task_id),
    )
    return {"ok": True, "send_result": send_result, "task_id": str(task_id)}

@app.post("/admin/email-drafts/{email_draft_id}/update")
async def admin_update_email_draft(email_draft_id: str, body: AdminUpdateEmailDraftRequest):
    updates = []
    params = []

    if body.subject is not None:
        updates.append("subject = %s")
        params.append(body.subject.strip())

    if body.summary is not None:
        updates.append("summary = %s")
        params.append(body.summary.strip())

    if body.draft_reply is not None:
        updates.append("draft_reply = %s")
        params.append(body.draft_reply.strip())

    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided to update")

    updates.append("status = 'needs_edit'")
    updates.append("updated_at = NOW()")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            query = f"""
                UPDATE email_drafts
                SET {", ".join(updates)}
                WHERE id = %s
                RETURNING id, owner_user_id, sender_email, subject, summary, draft_reply, status
            """
            params.append(email_draft_id)
            cur.execute(query, tuple(params))
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=404, detail="Email draft not found")

            write_audit_log(
                cur,
                user_id=row[1],
                action_type="email.draft_updated_by_admin",
                object_type="email_draft",
                object_id=row[0],
                recommendation_summary=row[4],
                human_decision="admin_update_email_draft",
            )

        conn.commit()

    resend_result = None
    if body.resend_to_telegram:
        resend_result = await resend_email_draft_approval_card(email_draft_id)

    return {
        "ok": True,
        "email_draft_id": str(row[0]),
        "status": row[6],
        "subject": row[3],
        "summary": row[4],
        "draft_reply": row[5],
        "resent": body.resend_to_telegram,
        "resend_result": resend_result,
    }


@app.get("/admin/email-drafts")
def admin_get_email_drafts(
    status: str | None = None,
    owner_user_id: str | None = None,
    sender_email: str | None = None,
    thread_id: str | None = None,
    contact_email: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
):
    limit = max(1, min(limit, 200))

    query = """
        SELECT
            d.id,
            d.owner_user_id,
            u.email,
            d.sender_email,
            d.subject,
            d.summary,
            d.draft_reply,
            d.status,
            d.thread_id,
            d.contact_email,
            d.project_id,
            d.created_at,
            d.updated_at
        FROM email_drafts d
        LEFT JOIN users u ON u.id = d.owner_user_id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND d.status = %s"
        params.append(status)

    if owner_user_id:
        query += " AND d.owner_user_id = %s"
        params.append(owner_user_id)

    if sender_email:
        query += " AND lower(d.sender_email) = %s"
        params.append(sender_email.strip().lower())

    if thread_id:
        query += " AND d.thread_id = %s"
        params.append(thread_id.strip())

    if contact_email:
        query += " AND lower(d.contact_email) = %s"
        params.append(contact_email.strip().lower())

    if project_id:
        query += " AND d.project_id = %s"
        params.append(project_id.strip())

    query += " ORDER BY d.updated_at DESC LIMIT %s"
    params.append(limit)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

    return {
        "ok": True,
        "count": len(rows),
        "email_drafts": [
            {
                "id": str(row[0]),
                "owner_user_id": str(row[1]) if row[1] else None,
                "owner_email": row[2],
                "sender_email": row[3],
                "subject": row[4],
                "summary": row[5],
                "draft_reply": row[6],
                "status": row[7],
                "thread_id": row[8],
                "contact_email": row[9],
                "project_id": row[10],
                "created_at": row[11].isoformat() if row[11] else None,
                "updated_at": row[12].isoformat() if row[12] else None,
            }
            for row in rows
        ],
    }


@app.get("/admin/approvals/{approval_id}")
def admin_get_approval_detail(approval_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    a.id,
                    a.user_id,
                    u.email,
                    a.approval_type,
                    a.target_object_type,
                    a.target_object_id,
                    a.status,
                    a.requested_at,
                    a.approved_at,
                    a.rejected_at,
                    a.created_at,
                    a.updated_at,
                    d.id,
                    d.sender_email,
                    d.subject,
                    d.summary,
                    d.draft_reply,
                    d.status,
                    d.thread_id,
                    d.contact_email,
                    d.project_id,
                    d.created_at,
                    d.updated_at
                FROM approvals a
                LEFT JOIN users u
                    ON u.id = a.user_id
                LEFT JOIN email_drafts d
                    ON d.id = a.target_object_id
                WHERE a.id = %s
                LIMIT 1
                """,
                (approval_id,),
            )
            row = cur.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Approval not found")

    return {
        "ok": True,
        "approval": {
            "id": str(row[0]),
            "user_id": str(row[1]) if row[1] else None,
            "owner_email": row[2],
            "approval_type": row[3],
            "target_object_type": row[4],
            "target_object_id": str(row[5]) if row[5] else None,
            "status": row[6],
            "requested_at": row[7].isoformat() if row[7] else None,
            "approved_at": row[8].isoformat() if row[8] else None,
            "rejected_at": row[9].isoformat() if row[9] else None,
            "created_at": row[10].isoformat() if row[10] else None,
            "updated_at": row[11].isoformat() if row[11] else None,
        },
        "email_draft": {
            "id": str(row[12]) if row[12] else None,
            "sender_email": row[13],
            "subject": row[14],
            "summary": row[15],
            "draft_reply": row[16],
            "status": row[17],
            "thread_id": row[18],
            "contact_email": row[19],
            "project_id": row[20],
            "created_at": row[21].isoformat() if row[21] else None,
            "updated_at": row[22].isoformat() if row[22] else None,
        } if row[12] else None,
    }


@app.get("/admin/approvals")
def admin_get_approvals(
    status: str | None = None,
    user_id: str | None = None,
    approval_type: str | None = None,
    thread_id: str | None = None,
    contact_email: str | None = None,
    project_id: str | None = None,
    limit: int = 50,
):
    limit = max(1, min(limit, 200))

    query = """
        SELECT
            a.id,
            a.user_id,
            u.email,
            a.approval_type,
            a.target_object_type,
            a.target_object_id,
            a.status,
            a.requested_at,
            a.approved_at,
            a.rejected_at,
            a.created_at,
            a.updated_at
        FROM approvals a
        LEFT JOIN users u ON u.id = a.user_id
        LEFT JOIN email_drafts d ON d.id = a.target_object_id
        WHERE 1=1
    """
    params = []

    if status:
        query += " AND a.status = %s"
        params.append(status)

    if user_id:
        query += " AND a.user_id = %s"
        params.append(user_id)

    if approval_type:
        query += " AND a.approval_type = %s"
        params.append(approval_type)

    if thread_id:
        query += " AND d.thread_id = %s"
        params.append(thread_id.strip())

    if contact_email:
        query += " AND lower(d.contact_email) = %s"
        params.append(contact_email.strip().lower())

    if project_id:
        query += " AND d.project_id = %s"
        params.append(project_id.strip())

    query += " ORDER BY a.updated_at DESC LIMIT %s"
    params.append(limit)

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(query, tuple(params))
            rows = cur.fetchall()

    return {
        "ok": True,
        "count": len(rows),
        "approvals": [
            {
                "id": str(row[0]),
                "user_id": str(row[1]) if row[1] else None,
                "owner_email": row[2],
                "approval_type": row[3],
                "target_object_type": row[4],
                "target_object_id": str(row[5]) if row[5] else None,
                "status": row[6],
                "requested_at": row[7].isoformat() if row[7] else None,
                "approved_at": row[8].isoformat() if row[8] else None,
                "rejected_at": row[9].isoformat() if row[9] else None,
                "created_at": row[10].isoformat() if row[10] else None,
                "updated_at": row[11].isoformat() if row[11] else None,
            }
            for row in rows
        ],
    }


SPAM_BLOCKED_SENDERS = {
    "mailer-daemon@",
    "postmaster@",
    "noreply@",
    "no-reply@",
    "donotreply@",
    "do-not-reply@",
    "notifications@",
    "bounce@",
    "unsubscribe@",
    "auto-reply@",
    "autoreply@",
    "noreply@github",
    "noreply@supabase",
    "noreply@vercel",
    "noreply@stripe",
    "noreply@twilio",
    "noreply@digitalocean",
    "noreply@sendgrid",
    "noreply@mailchimp",
    "noreply@hubspot",
    "noreply@linkedin",
    "noreply@twitter",
    "noreply@facebook",
    "noreply@google",
    "noreply@zoom",
    "noreply@slack",
    "noreply@notion",
    "noreply@calendly",
}

SPAM_SUBJECT_KEYWORDS = [
    "unsubscribe",
    "click here",
    "you have been selected",
    "congratulations you won",
    "limited time offer",
    "act now",
    "free money",
    "make money fast",
    "nigerian prince",
    "wire transfer",
    "invoice attached",
    "your account has been suspended",
    "verify your account",
    "confirm your email",
    "password reset",
    "delivery failed",
    "mailer-daemon",
    "unsubscribe from",
    "opt out",
    "email preferences",
    "manage your subscription",
    "you're receiving this",
    "this is an automated",
    "do not reply to this",
    "this email was sent to",
    "update your preferences",
    "workflow run",
    "build failed",
    "build succeeded",
    "deployment failed",
    "deployment succeeded",
    "pull request",
    "github actions",
    "security alert",
    "sign-in attempt",
    "new login detected",
    "your invoice",
    "receipt for your",
    "payment received",
    "payment failed",
]

def is_spam_email(sender_email: str, subject: str) -> tuple[bool, str]:
    sender_lower = sender_email.strip().lower()
    subject_lower = subject.strip().lower()

    for blocked in SPAM_BLOCKED_SENDERS:
        if sender_lower.startswith(blocked) or blocked in sender_lower:
            return True, f"blocked_sender_pattern:{blocked}"

    for kw in SPAM_SUBJECT_KEYWORDS:
        if kw in subject_lower:
            return True, f"spam_subject_keyword:{kw}"

    return False, ""


@app.get("/track/email-open/{draft_id}", include_in_schema=False)
async def track_email_open(draft_id: str):
    import base64
    from fastapi.responses import Response as FastAPIResponse
    gif_bytes = base64.b64decode(
        "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7"
    )
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_logs (tenant_id, action_type, object_type, object_id, execution_status) VALUES ('skopi', 'email_opened', 'email_draft', %s::uuid, 'success')",
                    (draft_id,),
                )
                conn.commit()
    except Exception:
        pass
    return FastAPIResponse(content=gif_bytes, media_type="image/gif")


@app.post("/ingest/email/test")
async def ingest_email_test(body: IngestEmailRequest):
    owner_email = body.owner_email.strip().lower()
    sender_email = body.sender_email.strip()
    subject = body.subject.strip()
    raw_body = body.body.strip()
    thread_id = body.thread_id.strip() if body.thread_id else None
    contact_email = body.contact_email.strip().lower() if body.contact_email else None
    project_id = body.project_id.strip() if body.project_id else None

    filtered, filter_reason = is_spam_email(sender_email, subject)
    if filtered:
        return {
            "ok": False,
            "filtered": True,
            "reason": filter_reason,
            "sender_email": sender_email,
            "subject": subject,
        }

    processed = build_email_summary_and_draft_ai_first(
        subject,
        raw_body,
        thread_id=thread_id,
        contact_email=contact_email,
        project_id=project_id,
    )

    result = await create_email_approval_for_owner(
        owner_email=owner_email,
        sender_email=sender_email,
        subject=subject,
        summary=processed["summary"],
        draft_reply=processed["draft_reply"],
        thread_id=thread_id,
        contact_email=contact_email,
        project_id=project_id,
    )

    return {
        "ok": True,
        "ingest_type": "test_email_payload",
        "generation_mode": processed["generation_mode"],
        "thread_id": thread_id,
        "contact_email": contact_email,
        "project_id": project_id,
        "generated_summary": processed["summary"],
        "generated_draft_reply": processed["draft_reply"],
        **result,
    }


@app.post("/ingest/skopi-event")
async def ingest_skopi_event(request: Request):
    secret = os.getenv("SKOPI_WEBHOOK_SECRET", "").strip()
    incoming = request.headers.get("x-skopi-secret", "").strip()
    if not secret or incoming != secret:
        raise HTTPException(status_code=401, detail="Invalid or missing webhook secret")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = payload.get("event_type", "unknown")
    source_entity_type = payload.get("source_entity_type")
    source_entity_id = payload.get("source_entity_id")
    event_key = payload.get("event_key")

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO app_skopi_events (
                    event_key, event_type, source_entity_type,
                    source_entity_id, raw_payload_json, processing_status
                ) VALUES (%s, %s, %s, %s, %s, 'received')
                ON CONFLICT (event_key) DO NOTHING
                RETURNING id
                """,
                (
                    event_key,
                    event_type,
                    source_entity_type,
                    source_entity_id,
                    json.dumps(payload),
                ),
            )
            row = cur.fetchone()
            conn.commit()

    if row:
        return {"ok": True, "event_id": str(row[0]), "status": "recorded"}
    else:
        return {"ok": True, "status": "duplicate_skipped"}

@app.post("/telegram/test/send-email-approval")
async def telegram_test_send_email_approval(
    owner_email: str | None = None,
    subject: str | None = None,
    sender: str | None = None,
):
    owner_email = (owner_email or "iosifskorohodov@gmail.com").strip().lower()
    subject = (subject or "Re: Utility timing for Reindeer project").strip()
    sender = (sender or "john.peterson@example.com").strip()

    summary = "John is asking for an updated utility timing estimate. Medium priority. A reply today is reasonable."
    draft_reply = "Hi John, thanks for checking in. We are reviewing the current utility timing and I will send you an updated estimate shortly. Best,"

    result = await create_email_approval_for_owner(
        owner_email=owner_email,
        sender_email=sender,
        subject=subject,
        summary=summary,
        draft_reply=draft_reply,
    )

    return {
        "ok": True,
        "subject": subject,
        "sender": sender,
        **result,
    }

@app.post("/telegram/test/send-reminder-card")
async def telegram_test_send_reminder_card():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT telegram_user_id
                FROM users
                WHERE email = %s
                LIMIT 1
                """,
                ("iosifskorohodov@gmail.com",),
            )
            user_row = cur.fetchone()
            if not user_row or not user_row[0]:
                raise HTTPException(status_code=400, detail="No linked telegram_user_id found for default user")

            chat_id = int(user_row[0])

            cur.execute(
                """
                SELECT id, title, next_trigger_at
                FROM reminders
                WHERE user_id = (
                    SELECT id FROM users WHERE email = %s LIMIT 1
                )
                ORDER BY created_at DESC
                LIMIT 1
                """,
                ("iosifskorohodov@gmail.com",),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="No reminders found to send")

            reminder_id, title, next_trigger_at = row

    text = build_reminder_card_text(title, next_trigger_at)
    send_result = await send_telegram_message(
        chat_id,
        text,
        reply_markup=build_reminder_reply_markup(reminder_id),
    )
    return {"ok": True, "send_result": send_result, "reminder_id": str(reminder_id)}

@app.post("/ingest/telegram-update")
async def ingest_telegram_update(request: Request):
    payload = await request.json()
    return await process_telegram_payload(payload)

async def process_telegram_callback_query(callback_query: dict, response_bot_token: str | None = None):
    callback_query_id = callback_query.get("id")
    data = (callback_query.get("data") or "").strip()
    message = callback_query.get("message", {}) or {}
    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")

    if not data:
        if callback_query_id:
            await answer_telegram_callback_query(callback_query_id, "No action found.")
        return {"ok": False, "reason": "missing_callback_data"}

    if ":" in data:
        action, reminder_id = data.split(":", 1)
    else:
        action = data
        reminder_id = None

    # ── TERRA-FIELD-7C-2: parcel button callback handlers ──────────────
    if action.startswith("parcel_") and _TERRA_DIAL_AVAILABLE:
        # data format: 'parcel_permits:<taxlot>'
        taxlot = reminder_id  # the part after ':' was already extracted upstream
        if not taxlot:
            if callback_query_id:
                await answer_telegram_callback_query(callback_query_id, "No taxlot in callback data.")
            return {"ok": False, "reason": "missing_taxlot"}

        # Acknowledge the tap immediately (Telegram shows loading toast)
        ack_msg = {
            "parcel_permits":   "🔥 Scanning permits…",
            "parcel_sales":     "💰 Pulling sales history…",
            "parcel_valuation": "📊 Loading valuation trend…",
            "parcel_devdocs":   "📁 Checking development docs…",
        }.get(action, "🛰️ Working…")
        if callback_query_id:
            await answer_telegram_callback_query(callback_query_id, ack_msg)

        # Route to the matching DIAL fetcher
        try:
            if action == "parcel_permits":
                _result = fetch_dial_permits(taxlot)
                _tool_name = "fetch_dial_permits"
            elif action == "parcel_sales":
                _result = fetch_dial_sales(taxlot)
                _tool_name = "fetch_dial_sales"
            elif action == "parcel_valuation":
                _result = fetch_dial_valuation(taxlot)
                _tool_name = "fetch_dial_valuation"
            elif action == "parcel_devdocs":
                _result = fetch_dial_dev_docs(taxlot)
                _tool_name = "fetch_dial_dev_docs"
            else:
                if chat_id:
                    await send_telegram_message(chat_id, f"⚠️ Unknown parcel action: {action}")
                return {"ok": False, "reason": f"unknown_parcel_action:{action}"}
        except Exception as _terra_err:
            print(f"[parcel_callback] DIAL fetch error for {action}/{taxlot}: {_terra_err}")
            if chat_id:
                await send_telegram_message(chat_id, f"⚠️ TERRA fetch error: {_terra_err}")
            return {"ok": False, "reason": "dial_fetch_error"}

        # Hand the raw result to the engine for TERRA-hype formatting
        try:
            from children.child_engine import ask_child as _engine_ask
            import json as _json
            from_user = callback_query.get("from", {}) or {}
            sender_telegram_id = str(from_user.get("id", ""))
            child_name = "ophelia" if (callable(globals().get("is_dan")) and is_dan(sender_telegram_id)) else "sophia"

            format_prompt = (
                f"The user tapped the {action.replace('parcel_', '').upper()} button on a TERRA parcel card "
                f"for taxlot `{taxlot}`. The {_tool_name} tool returned this raw data:\n\n"
                f"```json\n{_json.dumps(_result, indent=2, default=str)[:6000]}\n```\n\n"
                f"Format this for the user using the FULL TERRA aesthetic — stars, dividers, emoji, "
                f"section headers, max hype. Keep it scannable. End with one specific suggested follow-up. "
                f"Do NOT call any tools — just format the data above."
            )
            _formatted = await _engine_ask(child_name, user_message=format_prompt)
        except Exception as _fmt_err:
            print(f"[parcel_callback] formatting error: {_fmt_err}")
            import json as _json2
            _formatted = (
                f"🛰️ *TERRA — {action.replace('parcel_','').upper()}*\n\n"
                f"Taxlot: `{taxlot}`\n\n"
                f"```\n{_json2.dumps(_result, indent=2, default=str)[:3500]}\n```"
            )

        # Re-attach the parcel keyboard so user can chain to next layer
        # (extract property_id from the formatted reply if available, same
        # regex helper used at TERRA SCAN COMPLETE time)
        try:
            _chain_prop_id = _terra_extract_property_id(_formatted)
        except Exception:
            _chain_prop_id = None
        _chain_kb = _terra_build_parcel_keyboard(taxlot, _chain_prop_id)

        try:
            if child_name == "sophia":
                await send_sophia_message_with_markup(chat_id, _formatted, _chain_kb)
            else:
                await send_ophelia_message_with_markup(chat_id, _formatted, _chain_kb)
            print(f"[parcel_callback] OK: action={action} taxlot={taxlot} child={child_name}")
        except Exception as _send_err:
            print(f"[parcel_callback] send error: {_send_err} (action={action})")
            if chat_id:
                # Last-resort fallback: plain message, no keyboard
                await send_telegram_message(chat_id, _formatted)

        return {"ok": True, "action": action, "taxlot": taxlot}
    # ── END TERRA-FIELD-7C-2 parcel branches ────────────────────────────

    result_text = "Action received."
    action_type = None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            if action == "reminder_done":
                cur.execute(
                    """
                    UPDATE reminders
                    SET status = 'completed', updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, title
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                if row:
                    action_type = "reminder.completed_from_button"
                    result_text = f"Reminder completed: {row[1]}"
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="reminder",
                        object_id=row[0],
                        human_decision="telegram_inline_button_done",
                    )

            elif action == "reminder_snooze_60":
                cur.execute(
                    """
                    UPDATE reminders
                    SET status = 'pending',
                        next_trigger_at = COALESCE(next_trigger_at, NOW()) + interval '1 hour',
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, title, next_trigger_at
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                if row:
                    action_type = "reminder.snoozed_from_button"
                    next_text = row[2].isoformat() if row[2] else "later"
                    result_text = f"Reminder snoozed 1h: {row[1]}\nNext: {next_text}"
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="reminder",
                        object_id=row[0],
                        human_decision="telegram_inline_button_snooze_1h",
                    )

            elif action == "task_done":
                cur.execute(
                    """
                    UPDATE tasks
                    SET status = 'completed',
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, title, status
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                if row:
                    action_type = "task.completed_from_button"
                    result_text = f"Task completed: {row[1]}"
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="task",
                        object_id=row[0],
                        human_decision="telegram_inline_button_task_done",
                    )

            elif action in ("task_estimate_15", "task_estimate_30", "task_estimate_60", "task_estimate_90", "task_estimate_120"):
                minutes = int(action.rsplit("_", 1)[1])

                cur.execute(
                    """
                    UPDATE tasks
                    SET estimated_duration_minutes = %s,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, title, estimated_duration_minutes
                    """,
                    (minutes, reminder_id),
                )
                row = cur.fetchone()
                if row:
                    action_type = "task.estimated_from_button"
                    result_text = f"Estimate saved: {row[1]} = {row[2]} minutes"

                    cur.execute(
                        """
                        UPDATE daily_plan_items
                        SET estimated_duration_minutes = %s,
                            accepted_duration_minutes = %s,
                            updated_at = NOW()
                        WHERE task_id = %s
                        """,
                        (minutes, minutes, reminder_id),
                    )

                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="task",
                        object_id=row[0],
                        human_decision=f"telegram_inline_button_{minutes}m",
                    )
                else:
                    result_text = "Task not found."

            elif action == "day_review_estimates":
                cur.execute(
                    """
                    SELECT id, title, status, priority
                    FROM tasks
                    WHERE user_id = %s
                      AND status NOT IN ('completed', 'canceled', 'archived')
                      AND estimated_duration_minutes IS NULL
                    ORDER BY created_at DESC
                    LIMIT 10
                    """,
                    (user_id,),
                )
                missing_tasks = cur.fetchall()

                action_type = "day.review_estimates_from_button"

                if missing_tasks:
                    result_text = f"Sending {len(missing_tasks)} task(s) for estimate review."
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="daily_plan",
                        human_decision="telegram_inline_button_day_review_estimates",
                    )
                else:
                    result_text = "No tasks are missing estimates."

            elif action == "email_send":
                cur.execute(
                    """
                    UPDATE approvals
                    SET status = 'approved',
                        approved_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, target_object_id, status
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                action_type = "email.sent_from_button"
                smtp_payload = None
                if row:
                    approval_id = row[0]
                    email_draft_id = row[1]
                    if email_draft_id:
                        cur.execute(
                            """
                            UPDATE email_drafts
                            SET status = 'approved_for_send', updated_at = NOW()
                            WHERE id = %s
                            """,
                            (email_draft_id,),
                        )
                        cur.execute(
                            "SELECT sender_email, subject, draft_reply FROM email_drafts WHERE id = %s LIMIT 1",
                            (email_draft_id,),
                        )
                        draft_row = cur.fetchone()
                        if draft_row:
                            smtp_payload = {
                                "to_addr": draft_row[0],
                                "subject": draft_row[1],
                                "body": draft_row[2],
                                "draft_id": email_draft_id,
                            }
                            result_text = f"Sending email to {draft_row[0]}..."
                        else:
                            result_text = "Email draft not found."
                    else:
                        result_text = "No draft linked to approval."
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="approval",
                        object_id=approval_id,
                        human_decision="telegram_inline_button_email_send",
                    )
                else:
                    result_text = "Approval not found."

            elif action == "email_edit":
                cur.execute(
                    """
                    SELECT id, target_object_id
                    FROM approvals
                    WHERE id = %s
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                action_type = "email.edit_requested_from_button"
                if row:
                    approval_id = row[0]
                    email_draft_id = row[1]

                    if email_draft_id:
                        cur.execute(
                            """
                            UPDATE email_drafts
                            SET status = 'needs_edit',
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (email_draft_id,),
                        )

                    result_text = "Edit requested. Stub only for now."
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="approval",
                        object_id=approval_id,
                        human_decision="telegram_inline_button_email_edit",
                    )
                else:
                    result_text = "Approval not found."

            elif action == "change_yes":
                change_id = reminder_id
                action_type = "change.confirmed_by_dan"
                result_text = f"✅ Change confirmed: {change_id}"
                write_audit_log(cur, user_id=user_id, action_type=action_type,
                                object_type="change", object_id=change_id,
                                human_decision="dan_confirmed_yes")
                msg_id = message.get("message_id")
                orig_text = message.get("text", "Change")
                confirmed_time = datetime.now(timezone.utc).strftime("%b %d %H:%M")

                # Grey out the card in Dan's Telegram
                if chat_id and msg_id:
                    await edit_telegram_message(
                        chat_id, msg_id,
                        f"{orig_text}\n\n_✅ Confirmed by Dan — {confirmed_time} UTC_",
                        reply_markup={"inline_keyboard": []}
                    )

                # Auto-create Deck card on board 2
                # Map change_id to card details
                deck_cards = {
                    "airdrop_date": ("🎯 Main Airdrop Campaign Launch — May 5", "CONFIRMED BY DAN. Full airdrop goes live May 5 2026. All systems must be ready: affiliate attribution, social channels, placement partners paid, creative assets deployed.", "🔴 To Do", "2026-05-05"),
                    "twitter_airdrop": ("🐦 X/Twitter Airdrop — May 4", "CONFIRMED BY DAN. X/Twitter airdrop launches May 4 — one day before main campaign. Warmup push. Part of $3,000 airdrop placement budget.", "🔴 To Do", "2026-05-04"),
                    "office_search": ("🏢 Redmond Office Search — Move In by May 1", "CONFIRMED BY DAN. Find apartment or rental in Redmond for SKOpi local presence. Iosif leading. Viewings Tuesday April 28. Goal: moved in by May 1.", "🔵 In Progress", "2026-05-01"),
                    "drone_shoot": ("🚁 Drone Shoot — Timothy Park — April 28 3-4 PM", "CONFIRMED BY DAN. Timothy Park (503-407-5853). Tuesday April 28 3-4 PM. 400ft airspace clearance being filed. Estimate tomorrow before noon. Deposit required. Budget $1,000.", "🔴 To Do", "2026-04-28"),
                    "bonneville_email": ("⚡ Bonneville Power Approval — Plat Draft", "CONFIRMED BY DAN. Send rough draft plat to Bonneville Power today. Critical land advancement. Dan is point person on follow-up.", "🔵 In Progress", "2026-04-22"),
                    "surveyor_deposit": ("📐 Surveyor Deposit — DAN RESPONSIBLE — May 12", "CONFIRMED BY DAN. Dan Wikert solely responsible for identifying surveyor, securing scheduling, paying $2,500 deposit by May 12 — one week after launch.", "🔴 To Do", "2026-05-12"),
                    "bella_contract": ("👩‍💼 Bella Contractor — May 2", "CONFIRMED BY DAN. Bella contractor integration May 2 — 3 days before airdrop. No action before then.", "🔴 To Do", "2026-05-02"),
                    "bluevine_transfer": ("💸 Dan BlueVine Transfers — $32,000 Remaining", "CONFIRMED BY DAN. $5,000 received April 22. $32,000 still owed. $5,000 per transfer limit. Weekly reminder to Dan on next transfer.", "✅ Complete", "2026-04-22"),
                }

                if change_id in deck_cards:
                    title, desc, stack_name, due = deck_cards[change_id]
                    try:
                        import urllib.request as ureq
                        import base64 as b64
                        deck_base = "https://office.skopi.io/index.php/apps/deck/api/v1.0"
                        deck_creds = b64.b64encode(b"admin:ORBadmin2024!").decode()
                        deck_headers = {
                            "Authorization": f"Basic {deck_creds}",
                            "Content-Type": "application/json",
                            "OCS-APIREQUEST": "true"
                        }
                        # Get stacks
                        sreq = ureq.Request(f"{deck_base}/boards/2/stacks", headers=deck_headers)
                        with ureq.urlopen(sreq) as r:
                            stacks_data = json.loads(r.read())
                        stack_id = None
                        for s in stacks_data:
                            if stack_name in s['title']:
                                stack_id = s['id']
                                break
                        if stack_id:
                            card_data = json.dumps({
                                "title": title, "description": desc,
                                "type": "plain", "order": 999,
                                "duedate": f"{due}T00:00:00+00:00"
                            }).encode()
                            creq = ureq.Request(
                                f"{deck_base}/boards/2/stacks/{stack_id}/cards",
                                data=card_data, headers=deck_headers, method="POST"
                            )
                            with ureq.urlopen(creq) as r:
                                card_result = json.loads(r.read())
                            if "id" in card_result:
                                logger.info("Deck card created for change %s: %s", change_id, title)
                    except Exception as deck_err:
                        logger.error("Deck card creation failed for %s: %s", change_id, deck_err)

                # Notify Iosif via Sophia
                with get_db_connection() as conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute("SELECT telegram_user_id FROM users WHERE email = %s LIMIT 1",
                                     ("iosif@skopi.io",))
                        row2 = cur2.fetchone()
                        if row2 and row2[0]:
                            await send_sophia_message(int(row2[0]),
                                f"*Sophia* — Dan confirmed: _{orig_text[:100]}_\n_Deck board updated automatically._")

            elif action == "change_no":
                # Dan flagged a concern — notify Iosif immediately
                change_id = reminder_id
                action_type = "change.flagged_by_dan"
                result_text = f"⚠️ Concern flagged: {change_id}"
                write_audit_log(cur, user_id=user_id, action_type=action_type,
                                object_type="change", object_id=change_id,
                                human_decision="dan_flagged_concern")
                msg_id = message.get("message_id")
                orig_text = message.get("text", "Change")
                if chat_id and msg_id:
                    await edit_telegram_message(
                        chat_id, msg_id,
                        f"{orig_text}\n\n_⚠️ Concern flagged — Iosif has been notified_",
                        reply_markup={"inline_keyboard": []}
                    )
                # Notify Iosif urgently via Sophia
                with get_db_connection() as conn2:
                    with conn2.cursor() as cur2:
                        cur2.execute("SELECT telegram_user_id FROM users WHERE email = %s LIMIT 1",
                                     ("iosif@skopi.io",))
                        row2 = cur2.fetchone()
                        if row2 and row2[0]:
                            await send_sophia_message(int(row2[0]),
                                f"*Sophia* ⚠️ — Dan flagged a concern on:\n\n_{orig_text[:80]}_\n\nPlease follow up with Dan directly.")

            elif action == "email_no_reply":
                cur.execute(
                    """
                    UPDATE approvals
                    SET status = 'no_reply',
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, target_object_id
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                action_type = "email.no_reply_from_button"
                if row:
                    approval_id = row[0]
                    email_draft_id = row[1]
                    if email_draft_id:
                        cur.execute(
                            "UPDATE email_drafts SET status = 'no_reply', updated_at = NOW() WHERE id = %s",
                            (email_draft_id,),
                        )
                    result_text = "✅ Marked as no response needed."
                    write_audit_log(cur, user_id=user_id, action_type=action_type,
                                    object_type="approval", object_id=approval_id,
                                    human_decision="telegram_inline_button_no_reply")
                    # Grey out the card
                    msg_id = message.get("message_id")
                    if chat_id and msg_id:
                        orig_text = message.get("text", "Email card")
                        await edit_telegram_message(
                            chat_id, msg_id,
                            f"~~{orig_text}~~\n\n_No response needed - marked {datetime.now(timezone.utc).strftime('%b %d %H:%M')} UTC_",
                            reply_markup={"inline_keyboard": [[
                                {"text": "🗑 Clear", "callback_data": f"card_clear:approval:{approval_id}"}
                            ]]}
                        )
                else:
                    result_text = "Approval not found."

            elif action in ("email_snooze_1", "email_snooze_12", "email_snooze_24"):
                hours = {"email_snooze_1": 1, "email_snooze_12": 12, "email_snooze_24": 24}[action]
                cur.execute(
                    """
                    UPDATE approvals
                    SET status = 'snoozed',
                        snooze_until = NOW() + (%s || ' hours')::interval,
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, target_object_id, snooze_until
                    """,
                    (str(hours), reminder_id),
                )
                row = cur.fetchone()
                action_type = "email.snoozed_from_button"
                if row:
                    approval_id = row[0]
                    snooze_until = row[2]
                    snooze_str = snooze_until.strftime("%b %d %H:%M UTC") if snooze_until else f"{hours}hr"
                    result_text = f"⏰ Snoozed until {snooze_str}"
                    write_audit_log(cur, user_id=user_id, action_type=action_type,
                                    object_type="approval", object_id=approval_id,
                                    human_decision=f"telegram_inline_button_snooze_{hours}h")
                    # Grey out the card
                    msg_id = message.get("message_id")
                    if chat_id and msg_id:
                        orig_text = message.get("text", "Email card")
                        await edit_telegram_message(
                            chat_id, msg_id,
                            f"{orig_text}\n\n_Back for review {snooze_str}_",
                            reply_markup={"inline_keyboard": []}
                        )
                else:
                    result_text = "Approval not found."

            elif action == "card_complete":
                # Universal mark complete — works for any card type
                parts = reminder_id.split(":") if reminder_id else []
                obj_type = parts[0] if len(parts) > 0 else "unknown"
                obj_id = parts[1] if len(parts) > 1 else reminder_id
                action_type = f"{obj_type}.completed_from_card"
                result_text = f"✅ Marked complete."
                write_audit_log(cur, user_id=user_id, action_type=action_type,
                                object_type=obj_type, object_id=obj_id,
                                human_decision="telegram_card_complete")
                msg_id = message.get("message_id")
                if chat_id and msg_id:
                    orig_text = message.get("text", "Card")
                    await edit_telegram_message(
                        chat_id, msg_id,
                        f"{orig_text}\n\n_Completed {datetime.now(timezone.utc).strftime('%b %d %H:%M')} UTC_",
                        reply_markup={"inline_keyboard": [[
                            {"text": "🗑 Clear", "callback_data": f"card_clear:{obj_type}:{obj_id}"}
                        ]]}
                    )

            elif action == "card_clear":
                # Delete the card entirely
                parts = reminder_id.split(":") if reminder_id else []
                obj_type = parts[0] if len(parts) > 0 else "unknown"
                obj_id = parts[1] if len(parts) > 1 else reminder_id
                action_type = f"{obj_type}.card_cleared"
                result_text = "Card cleared."
                write_audit_log(cur, user_id=user_id, action_type=action_type,
                                object_type=obj_type, object_id=obj_id,
                                human_decision="telegram_card_clear")
                msg_id = message.get("message_id")
                if chat_id and msg_id:
                    await delete_telegram_message(chat_id, msg_id)

            elif action == "email_cancel":
                cur.execute(
                    """
                    UPDATE approvals
                    SET status = 'rejected',
                        rejected_at = NOW(),
                        updated_at = NOW()
                    WHERE id = %s
                    RETURNING id, target_object_id, status
                    """,
                    (reminder_id,),
                )
                row = cur.fetchone()
                action_type = "email.canceled_from_button"
                if row:
                    approval_id = row[0]
                    email_draft_id = row[1]

                    if email_draft_id:
                        cur.execute(
                            """
                            UPDATE email_drafts
                            SET status = 'canceled',
                                updated_at = NOW()
                            WHERE id = %s
                            """,
                            (email_draft_id,),
                        )

                    result_text = "Email approval canceled."
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="approval",
                        object_id=approval_id,
                        human_decision="telegram_inline_button_email_cancel",
                    )
                else:
                    result_text = "Approval not found."

            elif action == "day_accept":
                plan_date = datetime.now(timezone.utc).date()

                cur.execute(
                    """
                    UPDATE daily_plans
                    SET status = 'accepted',
                        accepted_at = NOW(),
                        updated_at = NOW()
                    WHERE user_id = %s AND plan_date = %s
                    RETURNING id, plan_date, status, accepted_at, started_at
                    """,
                    (user_id, plan_date),
                )
                plan = cur.fetchone()

                action_type = "day.accepted_from_button"
                if plan:
                    result_text = "Day accepted."
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type=action_type,
                        object_type="daily_plan",
                        object_id=plan[0],
                        human_decision="telegram_inline_button_day_accept",
                    )
                else:
                    result_text = "No daily plan found for today."

            elif action == "day_start":
                plan_date = datetime.now(timezone.utc).date()

                cur.execute(
                    """
                    SELECT COUNT(*)
                    FROM tasks
                    WHERE user_id = %s
                      AND status NOT IN ('completed', 'canceled', 'archived')
                      AND estimated_duration_minutes IS NULL
                    """,
                    (user_id,),
                )
                missing_count = cur.fetchone()[0]

                action_type = "day.started_from_button"

                if missing_count > 0:
                    result_text = f"Cannot start day yet. {missing_count} open task(s) still need estimates."
                    write_audit_log(
                        cur,
                        user_id=user_id,
                        action_type="day.start_blocked_missing_estimates",
                        object_type="daily_plan",
                        human_decision=f"blocked_missing_estimates_{missing_count}",
                    )
                else:
                    cur.execute(
                        """
                        UPDATE daily_plans
                        SET status = 'started',
                            started_at = NOW(),
                            updated_at = NOW()
                        WHERE user_id = %s AND plan_date = %s
                        RETURNING id, plan_date, status, accepted_at, started_at
                        """,
                        (user_id, plan_date),
                    )
                    plan = cur.fetchone()

                    if plan:
                        result_text = "Day started."
                        write_audit_log(
                            cur,
                            user_id=user_id,
                            action_type=action_type,
                            object_type="daily_plan",
                            object_id=plan[0],
                            human_decision="telegram_inline_button_day_start",
                        )
                    else:
                        result_text = "No daily plan found for today."

        conn.commit()

    if callback_query_id:
        await answer_telegram_callback_query(callback_query_id, "Done")

    if chat_id:
        await send_telegram_message(chat_id, result_text)

        if action == "day_review_estimates":
            for task_row in missing_tasks:
                await send_telegram_message(
                    chat_id,
                    build_task_card_text(task_row[1], task_row[2], task_row[3]),
                    reply_markup=build_task_estimate_reply_markup(task_row[0]),
                )

    if action == "email_send" and smtp_payload:
        send_result = send_email_via_smtp(
            to_address=smtp_payload["to_addr"],
            subject="Re: " + smtp_payload["subject"],
            body=smtp_payload["body"],
            draft_id=smtp_payload["draft_id"],
        )
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                if send_result["ok"]:
                    cur.execute(
                        "UPDATE email_drafts SET status = 'sent', updated_at = NOW() WHERE id = %s",
                        (smtp_payload["draft_id"],),
                    )
                    if chat_id:
                        await send_telegram_message(chat_id, f"✅ Email sent to {smtp_payload['to_addr']}.")
                else:
                    cur.execute(
                        "UPDATE email_drafts SET status = 'send_failed', updated_at = NOW() WHERE id = %s",
                        (smtp_payload["draft_id"],),
                    )
                    if chat_id:
                        await send_telegram_message(chat_id, f"❌ Send failed: {send_result.get('error', 'unknown')}")
            conn.commit()

    return {"ok": True, "callback_data": data, "result_text": result_text}

async def process_telegram_payload(payload: dict):
    message = payload.get("message", {}) or {}
    text = (message.get("text") or "").strip()
    update_id = payload.get("update_id")
    from_user = message.get("from", {}) or {}
    telegram_user_id = from_user.get("id")
    telegram_username = from_user.get("username")
    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")

    summary = f"Telegram update {update_id}"
    if text:
        summary = f"Telegram message: {text[:200]}"

    task_created = False
    task_id = None
    reminder_created = False
    reminder_id = None
    owner_user_id = None
    reply_text = None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            owner_user_id = get_user_id_for_telegram(cur, telegram_user_id)

            if text.lower().startswith("/start"):
                cur.execute(
                    """
                    UPDATE users
                    SET telegram_user_id = %s, updated_at = NOW()
                    WHERE email IN (%s, %s)
                    RETURNING id
                    """,
                    (
                        str(telegram_user_id) if telegram_user_id is not None else None,
                        "iosifskorohodov@gmail.com",
                        "iosif@skopi.io",
                    ),
                )
                linked = cur.fetchone()
                if linked:
                    owner_user_id = linked[0]

            if text.lower().startswith("/help"):
                reply_text = build_help_text()

            cur.execute(
                """
                INSERT INTO events (
                    tenant_id,
                    user_id,
                    source_type,
                    event_type,
                    priority,
                    approval_required,
                    summary,
                    normalized_payload_json,
                    occurred_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    "skopi",
                    owner_user_id,
                    "telegram",
                    "message.telegram.received",
                    "normal",
                    False,
                    summary,
                    json.dumps(payload),
                    datetime.now(timezone.utc),
                ),
            )
            event_id = cur.fetchone()[0]

            if text.lower().startswith("/task "):
                title = text[6:].strip()
                if title:
                    cur.execute(
                        """
                        INSERT INTO tasks (
                            tenant_id,
                            user_id,
                            title,
                            description,
                            category,
                            status,
                            priority,
                            source_event_id,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        RETURNING id
                        """,
                        (
                            "skopi",
                            owner_user_id,
                            title,
                            f"Created from Telegram update {update_id}",
                            "telegram",
                            "created",
                            "normal",
                            event_id,
                        ),
                    )
                    task_id = cur.fetchone()[0]
                    task_created = True

                    write_audit_log(
                        cur,
                        user_id=owner_user_id,
                        event_id=event_id,
                        action_type="task.created",
                        object_type="task",
                        object_id=task_id,
                        human_decision="created_from_telegram",
                    )

            elif text.lower().startswith("/today"):
                reply_text = build_today_text(cur, owner_user_id)

            elif text.lower() == "/task":
                reply_text = "Usage: /task <title>\nExample: /task Call Fred about payout issue"

            elif text.lower() == "/remind":
                reply_text = (
                    "Usage: /remind <what> <when>\n"
                    "Examples:\n"
                    "/remind call Fred tomorrow morning\n"
                    "/remind investor follow-up tomorrow at 9\n"
                    "/remind check payout issue in 30 minutes"
                )


            elif text.lower() == "/kb":
                import os
                kb_dir = "/opt/nevsky-dev/kb"
                try:
                    files = sorted(os.listdir(kb_dir))
                    if files:
                        file_list = "\n".join(f"  {f}" for f in files)
                        reply_text = f"KB directory ({len(files)} files):\n{file_list}"
                    else:
                        reply_text = "KB directory is empty."
                except Exception as e:
                    reply_text = f"Could not read KB directory: {e}"

            elif text.startswith("/") and not (
                text.lower().startswith("/start")
                or text.lower().startswith("/help")
                or text.lower().startswith("/today")
                or text.lower().startswith("/task ")
                or text.lower() == "/task"
                or text.lower().startswith("/remind ")
                or text.lower() == "/remind"
                or text.lower() == "/kb"
            ):
                reply_text = "Unknown command. Send /help to see available commands."

            elif text.lower().startswith("/remind "):
                raw_reminder_text = text[8:].strip()
                if raw_reminder_text:
                    title, trigger_at = parse_simple_reminder_time(raw_reminder_text)
                    cur.execute(
                        """
                        INSERT INTO reminders (
                            tenant_id,
                            user_id,
                            title,
                            category,
                            status,
                            initial_trigger_at,
                            next_trigger_at,
                            ack_required,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        RETURNING id
                        """,
                        (
                            "skopi",
                            owner_user_id,
                            title,
                            "telegram",
                            "pending",
                            trigger_at,
                            trigger_at,
                            False,
                        ),
                    )
                    reminder_id = cur.fetchone()[0]
                    reminder_created = True

                    write_audit_log(
                        cur,
                        user_id=owner_user_id,
                        event_id=event_id,
                        action_type="reminder.created",
                        object_type="reminder",
                        object_id=reminder_id,
                        human_decision="created_from_telegram",
                    )

        conn.commit()

    return {
        "ok": True,
        "source": "telegram",
        "telegram_user_id": str(telegram_user_id) if telegram_user_id is not None else None,
        "telegram_username": telegram_username,
        "chat_id": str(chat_id) if chat_id is not None else None,
        "reply_text": reply_text,
        "event_id": str(event_id),
        "task_created": task_created,
        "task_id": str(task_id) if task_id else None,
        "reminder_created": reminder_created,
        "reminder_id": str(reminder_id) if reminder_id else None,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "top_level_keys": list(payload.keys()),
    }


# ============================================================
# KB SYNC — Item 85
# POST /kb/sync
# Accepts a file from Yakov (Claude) and writes it to /opt/nevsky-dev/kb/
# Auth: SKOPI_WEBHOOK_SECRET header
# ============================================================

class KBSyncRequest(BaseModel):
    filename: str
    content: str

class KBSyncResponse(BaseModel):
    status: str
    filename: str
    bytes_written: int
    path: str
    synced_at: str

@app.post("/kb/sync", response_model=KBSyncResponse)
async def kb_sync(request: KBSyncRequest, x_webhook_secret: str = Header(None)):
    import os, datetime

    # Auth check
    expected_secret = os.environ.get("SKOPI_WEBHOOK_SECRET", "")
    if not expected_secret or x_webhook_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Sanitize filename — no path traversal
    filename = os.path.basename(request.filename)
    if not filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Write file
    kb_dir = "/opt/nevsky-dev/kb"
    os.makedirs(kb_dir, exist_ok=True)
    file_path = os.path.join(kb_dir, filename)

    content_bytes = request.content.encode("utf-8")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(request.content)

    synced_at = datetime.datetime.utcnow().isoformat() + "Z"

    # Audit log
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                write_audit_log(
                    cur=cur,
                    user_id=None,
                    action_type="kb.file_synced",
                    object_type="kb_file",
                    object_id=filename,
                    recommendation_summary=f"Synced {len(content_bytes)} bytes to {file_path}"
                )
                conn.commit()
    except Exception as e:
        pass  # Don't fail the sync if audit log fails

    return KBSyncResponse(
        status="ok",
        filename=filename,
        bytes_written=len(content_bytes),
        path=file_path,
        synced_at=synced_at
    )


# ============================================================
# KB SYNC — Item 85
# POST /kb/sync
# Accepts a file from Yakov (Claude) and writes it to /opt/nevsky-dev/kb/
# Auth: SKOPI_WEBHOOK_SECRET header
# ============================================================

class KBSyncRequest(BaseModel):
    filename: str
    content: str

class KBSyncResponse(BaseModel):
    status: str
    filename: str
    bytes_written: int
    path: str
    synced_at: str

@app.post("/kb/sync", response_model=KBSyncResponse)
async def kb_sync(request: KBSyncRequest, x_webhook_secret: str = Header(None)):
    import os, datetime

    # Auth check
    expected_secret = os.environ.get("SKOPI_WEBHOOK_SECRET", "")
    if not expected_secret or x_webhook_secret != expected_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")

    # Sanitize filename — no path traversal
    filename = os.path.basename(request.filename)
    if not filename or filename.startswith("."):
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Write file
    kb_dir = "/opt/nevsky-dev/kb"
    os.makedirs(kb_dir, exist_ok=True)
    file_path = os.path.join(kb_dir, filename)

    content_bytes = request.content.encode("utf-8")
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(request.content)

    synced_at = datetime.datetime.utcnow().isoformat() + "Z"

    # Audit log
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                write_audit_log(
                    cur=cur,
                    user_id=None,
                    action_type="kb.file_synced",
                    object_type="kb_file",
                    object_id=filename,
                    recommendation_summary=f"Synced {len(content_bytes)} bytes to {file_path}"
                )
                conn.commit()
    except Exception as e:
        pass  # Don't fail the sync if audit log fails

    return KBSyncResponse(
        status="ok",
        filename=filename,
        bytes_written=len(content_bytes),
        path=file_path,
        synced_at=synced_at
    )

@app.post("/telegram/sophia/webhook")
async def sophia_webhook(request: Request):
    payload = await request.json()
    callback_query = payload.get("callback_query", {}) or {}
    if callback_query:
        return await process_telegram_callback_query(callback_query)
    message = payload.get("message", {}) or {}
    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    from_user = message.get("from", {}) or {}
    sender_telegram_id = str(from_user.get("id", ""))
    # === BLOCKLIST GUARD (sophia, added 2026-04-27, doctrine locked) ===
    try:
        _telegram_user_id = (message.get("from") or {}).get("id")
        if _telegram_user_id:
            _blocked = await check_blocklist_and_archive(
                telegram_user_id=_telegram_user_id,
                chat_id=chat_id or 0,
                message_text=text or "",
                bot_name="sophia",
                message_type="text" if message.get("text") else ("caption" if message.get("caption") else "other"),
                full_update=payload,
            )
            if _blocked:
                return {"ok": True, "blocked": True}
    except Exception as _e:
        print(f"[sophia_webhook] blocklist guard error: {_e}")
    # === END BLOCKLIST GUARD ===
    # === OWNER CHECK (sophia, added 2026-04-27) ===
    # Only the bound Telegram owner gets through to the LLM. Everyone else gets
    # a polite redirect and is logged to unauthorized_contact_log.
    try:
        if sender_telegram_id and sender_telegram_id != "7583693994":
            # Log the unauthorized contact attempt
            try:
                with get_db_connection() as _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            """INSERT INTO unauthorized_contact_log
                               (bot_canonical_name, telegram_user_id, telegram_username, chat_id, message_text, full_update_json)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            ("sophia", int(sender_telegram_id),
                             from_user.get("username"), chat_id or 0,
                             text or "", json.dumps(payload)),
                        )
                        _conn.commit()
            except Exception as _log_err:
                print(f"[sophia_webhook] unauthorized log error: {_log_err}")
            # Send a polite redirect
            if chat_id:
                await send_sophia_message(chat_id,
                "Hi — Sophia is Iosif Skorohodov's personal intelligence layer and isn't configured for outside conversations. "
                "If you're trying to reach SKOpi Global Holdings, please email iosif@skopi.io. Thanks for understanding.")
            return {"ok": True, "redirected": True}
    except Exception as _owner_err:
        print(f"[sophia_webhook] owner check error: {_owner_err}")
    # === END OWNER CHECK (sophia) ===


    # === ENGINE ROUTING (E2 Step 2, sophia, 2026-04-28) ===
    # === TERRA LOCATION PIN (sophia, 2026-04-28) ===
    _location = message.get("location") or {}
    _query_text = text
    if _location and _location.get("latitude") is not None and _location.get("longitude") is not None:
        _lat = _location["latitude"]
        _lon = _location["longitude"]
        _query_text = f"User dropped a Telegram location pin at coordinates {_lat}, {_lon}. What parcel is at this location? Use lookup_parcel_by_point and report findings clearly."
        print(f"[sophia_webhook] TERRA pin: lat={_lat} lon={_lon}")
    # === END TERRA LOCATION PIN (sophia) ===
    if chat_id:
        try:
            from children.child_engine import ask_child as _engine_ask, get_intro as _engine_intro
            if _query_text.lower() in ["/start", "hi", "hello", "hi sophia", "hello sophia", ""]:
                await send_sophia_message(chat_id, _engine_intro("sophia"))
            else:
                _reply = await _engine_ask("sophia", user_message=_query_text)
                # TERRA-FIELD-7C: detect parcel scan and add buttons
                _taxlot = _terra_extract_taxlot(_reply) if "TERRA SCAN COMPLETE" in _reply else None
                if _taxlot:
                    _prop_id = _terra_extract_property_id(_reply)
                    _kb = _terra_build_parcel_keyboard(_taxlot, _prop_id)
                    await send_sophia_message_with_markup(chat_id, _reply, _kb)
                else:
                    await send_sophia_message(chat_id, _reply)
        except Exception as _engine_err:
            print(f"[sophia_webhook] engine error: {_engine_err}")
            await send_sophia_message(chat_id, "Sophia hit an internal error. Yakov has been logged.")
    return {"ok": True}
    # === END ENGINE ROUTING (sophia) ===


@app.post("/telegram/ophelia/webhook")
async def ophelia_webhook(request: Request):
    payload = await request.json()
    callback_query = payload.get("callback_query", {}) or {}
    if callback_query:
        return await process_telegram_callback_query(callback_query)
    message = payload.get("message", {}) or {}
    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    from_user = message.get("from", {}) or {}
    sender_telegram_id = str(from_user.get("id", ""))
    # === BLOCKLIST GUARD (ophelia, added 2026-04-27, doctrine locked) ===
    try:
        _telegram_user_id = (message.get("from") or {}).get("id")
        if _telegram_user_id:
            _blocked = await check_blocklist_and_archive(
                telegram_user_id=_telegram_user_id,
                chat_id=chat_id or 0,
                message_text=text or "",
                bot_name="ophelia",
                message_type="text" if message.get("text") else ("caption" if message.get("caption") else "other"),
                full_update=payload,
            )
            if _blocked:
                return {"ok": True, "blocked": True}
    except Exception as _e:
        print(f"[ophelia_webhook] blocklist guard error: {_e}")
    # === END BLOCKLIST GUARD ===
    # === OWNER CHECK (ophelia, added 2026-04-27) ===
    # Only the bound Telegram owner gets through to the LLM. Everyone else gets
    # a polite redirect and is logged to unauthorized_contact_log.
    try:
        if sender_telegram_id and sender_telegram_id != "8058014097":
            # Log the unauthorized contact attempt
            try:
                with get_db_connection() as _conn:
                    with _conn.cursor() as _cur:
                        _cur.execute(
                            """INSERT INTO unauthorized_contact_log
                               (bot_canonical_name, telegram_user_id, telegram_username, chat_id, message_text, full_update_json)
                               VALUES (%s, %s, %s, %s, %s, %s)""",
                            ("ophelia", int(sender_telegram_id),
                             from_user.get("username"), chat_id or 0,
                             text or "", json.dumps(payload)),
                        )
                        _conn.commit()
            except Exception as _log_err:
                print(f"[ophelia_webhook] unauthorized log error: {_log_err}")
            # Send a polite redirect
            if chat_id:
                await send_ophelia_message(chat_id,
                "Hi — Ophelia is Dan Wikert's personal SKOpi assistant and isn't configured for outside conversations. "
                "If you're trying to reach SKOpi Global Holdings, please email iosif@skopi.io. Thanks for understanding.")
            return {"ok": True, "redirected": True}
    except Exception as _owner_err:
        print(f"[ophelia_webhook] owner check error: {_owner_err}")
    # === END OWNER CHECK (ophelia) ===


    # === ENGINE ROUTING (E2 Step 2, ophelia, 2026-04-28) ===
    # === TERRA LOCATION PIN (ophelia, 2026-04-28) ===
    _location = message.get("location") or {}
    _query_text = text
    if _location and _location.get("latitude") is not None and _location.get("longitude") is not None:
        _lat = _location["latitude"]
        _lon = _location["longitude"]
        _query_text = f"User dropped a Telegram location pin at coordinates {_lat}, {_lon}. What parcel is at this location? Use lookup_parcel_by_point and report findings clearly."
        print(f"[ophelia_webhook] TERRA pin: lat={_lat} lon={_lon}")
    # === END TERRA LOCATION PIN (ophelia) ===
    if chat_id:
        try:
            from children.child_engine import ask_child as _engine_ask, get_intro as _engine_intro
            if _query_text.lower() in ["/start", "hi", "hello", "hi ophelia", "hello ophelia", ""]:
                await send_ophelia_message(chat_id, _engine_intro("ophelia"))
            else:
                _reply = await _engine_ask("ophelia", user_message=_query_text)
                # TERRA-FIELD-7C: detect parcel scan and add buttons
                _taxlot = _terra_extract_taxlot(_reply) if "TERRA SCAN COMPLETE" in _reply else None
                if _taxlot:
                    _prop_id = _terra_extract_property_id(_reply)
                    _kb = _terra_build_parcel_keyboard(_taxlot, _prop_id)
                    await send_ophelia_message_with_markup(chat_id, _reply, _kb)
                else:
                    await send_ophelia_message(chat_id, _reply)
        except Exception as _engine_err:
            print(f"[ophelia_webhook] engine error: {_engine_err}")
            await send_ophelia_message(chat_id, "Ophelia hit an internal error. Iosif has been notified.")
    return {"ok": True}
    # === END ENGINE ROUTING (ophelia) ===


@app.post("/notify/changes")
async def notify_changes(request: Request):
    body = await request.json()
    changes = body.get("changes", [])
    if not changes:
        return {"ok": False, "reason": "no changes provided"}

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_user_id FROM users WHERE email = %s LIMIT 1",
                       ("daniel.wikert@skopi.io",))
            row = cur.fetchone()
            dan_chat_id = int(row[0]) if row and row[0] else None

    if not dan_chat_id:
        return {"ok": False, "reason": "Dan telegram_user_id not found"}

    await send_ophelia_message(dan_chat_id,
        "*Ophelia — SKOpi Update*\n\nHi Dan, Iosif has made some updates to the plan. Please review and confirm each one:")

    sent = 0
    for change in changes:
        change_id = change.get("id", f"chg_{sent}")
        title = change.get("title", "Update")
        description = change.get("description", "")
        text = f"*{title}*\n\n{description}"
        markup = {"inline_keyboard": [[
            {"text": "✅ Confirmed", "callback_data": f"change_yes:{change_id}"},
            {"text": "❌ Flag Concern", "callback_data": f"change_no:{change_id}"},
        ]]}
        await send_ophelia_message_with_markup(dan_chat_id, text, markup)
        sent += 1

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT telegram_user_id FROM users WHERE email = %s LIMIT 1",
                       ("iosif@skopi.io",))
            row = cur.fetchone()
            if row and row[0]:
                await send_sophia_message(int(row[0]),
                    f"*Sophia* — {sent} change notification(s) sent to Dan for review.")

    return {"ok": True, "sent": sent}

@app.post("/telegram/hypatia/webhook")
async def hypatia_webhook(request: Request):
    payload = await request.json()
    callback_query = payload.get("callback_query", {}) or {}
    if callback_query:
        hypatia_token = os.environ.get("HYPATIA_BOT_TOKEN", "")
        return await process_telegram_callback_query(callback_query, response_bot_token=hypatia_token)
    message = payload.get("message", {}) or {}
    chat = message.get("chat", {}) or {}
    chat_id = chat.get("id")
    text = (message.get("text") or "").strip()
    from_user = message.get("from", {}) or {}
    sender_id = str(from_user.get("id", ""))


    if chat_id:
        import os
        
        # === BLOCKLIST GUARD (hypatia, added 2026-04-27, doctrine locked) ===
        try:
            _telegram_user_id = (message.get("from") or {}).get("id")
            if _telegram_user_id:
                _blocked = await check_blocklist_and_archive(
                    telegram_user_id=_telegram_user_id,
                    chat_id=chat_id or 0,
                    message_text=text or "",
                    bot_name="hypatia",
                    message_type="text" if message.get("text") else ("caption" if message.get("caption") else "other"),
                    full_update=payload,
                )
                if _blocked:
                    return {"ok": True, "blocked": True}
        except Exception as _e:
            print(f"[hypatia_webhook] blocklist guard error: {_e}")
        # === END BLOCKLIST GUARD ===
        # === ENGINE ROUTING (E2 Step 2, hypatia, 2026-04-28) ===
        # Hypatia is decommissioned + honeypot tier. Blocklist catches Fred.
        # Anyone else who reaches this point gets silence — no welcome, no intro,
        # no LLM call. The bot is dead. The shell exists for evidence capture only.
        # === END ENGINE ROUTING (hypatia) ===
    return {"ok": True}


# === SITE_PLANS_ROUTES_START ===
# Added via patch_site_plans.py — Nevsky site plan ingestion
# Routes:
#   POST /site-plans/analyze        — upload PDF, Claude Opus extracts structured data
#   GET  /site-plans                — list all analyzed plans
#   GET  /site-plans/{plan_id}      — get single plan + lots + scenarios

SITE_PLANS_MODEL = os.getenv("NEVSKY_SITE_PLAN_MODEL", "claude-opus-4-5")
SITE_PLANS_MAX_PDF_BYTES = 25 * 1024 * 1024
SITE_PLANS_DEFAULT_4PLEX_PRICE = 500_000.00

SITE_PLANS_EXTRACTION_PROMPT = """You are analyzing a real-estate site plan / plat document for SKOpi Global Holdings' ORB (Nevsky). Extract structured data.

Return ONLY a single JSON object — no prose, no markdown fences, no commentary.
The schema you must match exactly:

{
  "project_name": string | null,
  "city": string | null,
  "state": string | null,
  "county": string | null,
  "apn": string | null,
  "street_address": string | null,
  "total_sqft": number | null,
  "total_acres": number | null,
  "zoning": string | null,
  "unit_count": number | null,
  "lot_count": number | null,
  "estimated_total_value": number | null,
  "estimated_per_lot_value": number | null,
  "has_utility_easement": boolean,
  "easement_description": string | null,
  "setback_notes": string | null,
  "executive_summary": string,
  "lots": [
    {
      "lot_label": string,
      "side": "west"|"east"|"north"|"south"|"center"|"other"|null,
      "width_ft": number | null,
      "depth_ft": number | null,
      "lot_sqft": number | null,
      "structure_footprint_sqft": number | null,
      "structure_type": string | null,
      "within_easement_buffer": boolean,
      "notes": string | null
    }
  ],
  "scenarios": [
    {
      "scenario_label": string,
      "description": string,
      "total_sqft": number | null,
      "lot_count": number | null,
      "unit_count": number | null,
      "gross_retail_value_usd": number | null,
      "per_unit_value_usd": number | null,
      "notes": string | null
    }
  ],
  "data_quality_notes": string | null
}

Rules:
- If a number is not shown, use null — do NOT guess.
- Convert feet-inches to decimal feet.
- If the plan shows ~5000 sqft structure pads in residential context, default structure_type to "4plex".
- If the plan's own math is inconsistent, note it in data_quality_notes.
- Return valid JSON only, nothing else.
"""


# Opus 4.x pricing (USD per 1M tokens) — update if Anthropic changes
SITE_PLANS_OPUS_IN_PER_MTOK = 15.00
SITE_PLANS_OPUS_OUT_PER_MTOK = 75.00


def _site_plans_calc_cost(tokens_in, tokens_out):
    return round(
        (tokens_in / 1_000_000) * SITE_PLANS_OPUS_IN_PER_MTOK
        + (tokens_out / 1_000_000) * SITE_PLANS_OPUS_OUT_PER_MTOK,
        6,
    )


def _site_plans_apply_pricing(lots):
    """Flat $500k per 4plex lot — Redmond rule."""
    for lot in lots or []:
        lot["retail_price_usd"] = SITE_PLANS_DEFAULT_4PLEX_PRICE
        lot["pricing_note"] = "Standard 4plex lot @ $500k (Redmond)"
        lot["is_price_override"] = False
    return lots or []


def _site_plans_analyze_pdf(pdf_bytes, filename):
    """Call Claude with the PDF; return parsed JSON dict + usage."""
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY not configured")
    client = Anthropic(api_key=api_key)

    b64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    try:
        resp = client.messages.create(
            model=SITE_PLANS_MODEL,
            max_tokens=8000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": b64,
                        },
                    },
                    {"type": "text", "text": SITE_PLANS_EXTRACTION_PROMPT},
                ],
            }],
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI analysis failed: {e}")

    text_parts = [b.text for b in resp.content if getattr(b, "type", "") == "text"]
    raw = "\n".join(text_parts).strip()

    # Strip accidental markdown fences
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.rsplit("```", 1)[0].strip()

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=502,
            detail=f"AI returned invalid JSON: {e}. First 500 chars: {raw[:500]}",
        )

    parsed["_usage"] = {
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }
    return parsed


@app.post("/site-plans/analyze")
async def site_plans_analyze(file: UploadFile = File(...)):
    """Upload a plat/site-plan PDF; Claude extracts lots, scenarios, valuation."""
    started_at = datetime.now(timezone.utc)

    if not file.content_type or file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Only application/pdf is supported")

    pdf_bytes = await file.read()
    if len(pdf_bytes) == 0:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(pdf_bytes) > SITE_PLANS_MAX_PDF_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"PDF exceeds {SITE_PLANS_MAX_PDF_BYTES // (1024*1024)}MB limit",
        )

    sha = hashlib.sha256(pdf_bytes).hexdigest()
    input_chars = len(pdf_bytes)

    # Dedup check
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM site_plans WHERE source_sha256 = %s LIMIT 1",
                (sha,),
            )
            row = cur.fetchone()
            if row:
                raise HTTPException(
                    status_code=409,
                    detail=f"PDF already analyzed: plan_id={row[0]}. GET /site-plans/{row[0]}",
                )

    # Run Claude analysis
    try:
        parsed = _site_plans_analyze_pdf(pdf_bytes, file.filename or "upload.pdf")
    except HTTPException:
        # Log failure + re-raise
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                uid = get_default_user_id(cur)
                write_ai_usage_log(
                    cur,
                    user_id=uid,
                    provider="anthropic",
                    model=SITE_PLANS_MODEL,
                    action_type="site_plan:analyze",
                    input_chars=input_chars,
                    status="error",
                    error_message="analysis_failed",
                )
                conn.commit()
        raise

    usage = parsed.pop("_usage", {})
    tokens_in = int(usage.get("input_tokens", 0))
    tokens_out = int(usage.get("output_tokens", 0))
    cost_usd = _site_plans_calc_cost(tokens_in, tokens_out)

    lots = _site_plans_apply_pricing(parsed.get("lots") or [])
    scenarios = parsed.get("scenarios") or []
    plan_id = str(uuid4())

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            uid = get_default_user_id(cur)

            cur.execute(
                """
                INSERT INTO site_plans (
                    id, tenant_id, uploaded_by, source_filename, source_mime_type,
                    source_sha256, project_name, city, state, county, apn, street_address,
                    total_sqft, total_acres, zoning, unit_count, lot_count,
                    estimated_total_value, estimated_per_lot_value,
                    has_utility_easement, easement_description, setback_notes,
                    ai_summary, ai_raw_json, analysis_model, analysis_cost_usd,
                    analysis_tokens_in, analysis_tokens_out, status
                ) VALUES (
                    %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s, %s, %s,
                    %s, %s, %s, %s, %s,
                    %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s,
                    %s, %s, 'analyzed'
                )
                """,
                (
                    plan_id, "skopi", uid, file.filename or "upload.pdf", "application/pdf",
                    sha, parsed.get("project_name"), parsed.get("city"), parsed.get("state"),
                    parsed.get("county"), parsed.get("apn"), parsed.get("street_address"),
                    parsed.get("total_sqft"), parsed.get("total_acres"), parsed.get("zoning"),
                    parsed.get("unit_count"), parsed.get("lot_count"),
                    parsed.get("estimated_total_value"), parsed.get("estimated_per_lot_value"),
                    bool(parsed.get("has_utility_easement", False)),
                    parsed.get("easement_description"), parsed.get("setback_notes"),
                    parsed.get("executive_summary"), json.dumps(parsed),
                    SITE_PLANS_MODEL, cost_usd, tokens_in, tokens_out,
                ),
            )

            for lot in lots:
                cur.execute(
                    """
                    INSERT INTO site_plan_lots (
                        site_plan_id, lot_label, side, width_ft, depth_ft, lot_sqft,
                        structure_footprint_sqft, structure_type,
                        retail_price_usd, pricing_note, is_price_override,
                        within_easement_buffer
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        plan_id, lot.get("lot_label"), lot.get("side"),
                        lot.get("width_ft"), lot.get("depth_ft"), lot.get("lot_sqft"),
                        lot.get("structure_footprint_sqft"), lot.get("structure_type"),
                        lot.get("retail_price_usd"), lot.get("pricing_note"),
                        bool(lot.get("is_price_override", False)),
                        bool(lot.get("within_easement_buffer", False)),
                    ),
                )

            for sc in scenarios:
                cur.execute(
                    """
                    INSERT INTO site_plan_scenarios (
                        site_plan_id, scenario_label, description,
                        total_sqft, lot_count, unit_count,
                        gross_retail_value_usd, per_unit_value_usd, notes, ai_raw_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        plan_id, sc.get("scenario_label") or "unlabeled",
                        sc.get("description"),
                        sc.get("total_sqft"), sc.get("lot_count"), sc.get("unit_count"),
                        sc.get("gross_retail_value_usd"), sc.get("per_unit_value_usd"),
                        sc.get("notes"), json.dumps(sc),
                    ),
                )

            write_ai_usage_log(
                cur,
                user_id=uid,
                provider="anthropic",
                model=SITE_PLANS_MODEL,
                action_type="site_plan:analyze",
                input_chars=input_chars,
                input_tokens=tokens_in,
                output_tokens=tokens_out,
                estimated_cost_usd=cost_usd,
                object_type="site_plan",
                object_id=plan_id,
                status="success",
            )
            conn.commit()

    elapsed = (datetime.now(timezone.utc) - started_at).total_seconds()

    return {
        "plan_id": plan_id,
        "project_name": parsed.get("project_name"),
        "city": parsed.get("city"),
        "state": parsed.get("state"),
        "total_sqft": parsed.get("total_sqft"),
        "lot_count": parsed.get("lot_count"),
        "unit_count": parsed.get("unit_count"),
        "estimated_total_value": parsed.get("estimated_total_value"),
        "executive_summary": parsed.get("executive_summary"),
        "data_quality_notes": parsed.get("data_quality_notes"),
        "lots_stored": len(lots),
        "scenarios_stored": len(scenarios),
        "analysis": {
            "duration_seconds": elapsed,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cost_usd": cost_usd,
            "model": SITE_PLANS_MODEL,
        },
    }


@app.get("/site-plans")
async def site_plans_list(limit: int = 50, offset: int = 0):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_filename, project_name, city, state,
                       total_acres, lot_count, unit_count,
                       estimated_total_value, analysis_cost_usd,
                       status, created_at
                FROM site_plans
                WHERE status != 'archived'
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                (limit, offset),
            )
            rows = cur.fetchall()

    return [
        {
            "id": str(r[0]),
            "source_filename": r[1],
            "project_name": r[2],
            "city": r[3],
            "state": r[4],
            "total_acres": float(r[5]) if r[5] is not None else None,
            "lot_count": r[6],
            "unit_count": r[7],
            "estimated_total_value": float(r[8]) if r[8] is not None else None,
            "analysis_cost_usd": float(r[9]) if r[9] is not None else None,
            "status": r[10],
            "created_at": r[11].isoformat() if r[11] else None,
        }
        for r in rows
    ]


@app.get("/site-plans/{plan_id}")
async def site_plans_get(plan_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, source_filename, project_name, city, state, county,
                       street_address, total_sqft, total_acres, zoning,
                       unit_count, lot_count, estimated_total_value,
                       estimated_per_lot_value, has_utility_easement,
                       easement_description, setback_notes, ai_summary,
                       ai_raw_json, analysis_model, analysis_cost_usd,
                       analysis_tokens_in, analysis_tokens_out, status, created_at
                FROM site_plans WHERE id = %s
                """,
                (plan_id,),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="Site plan not found")

            cur.execute(
                """
                SELECT id, lot_label, side, width_ft, depth_ft, lot_sqft,
                       structure_footprint_sqft, structure_type,
                       retail_price_usd, pricing_note, is_price_override,
                       within_easement_buffer
                FROM site_plan_lots WHERE site_plan_id = %s
                ORDER BY side NULLS LAST, lot_label
                """,
                (plan_id,),
            )
            lots = cur.fetchall()

            cur.execute(
                """
                SELECT id, scenario_label, description, total_sqft, lot_count,
                       unit_count, gross_retail_value_usd, per_unit_value_usd, notes
                FROM site_plan_scenarios WHERE site_plan_id = %s
                ORDER BY scenario_label
                """,
                (plan_id,),
            )
            scenarios = cur.fetchall()

    return {
        "id": str(row[0]),
        "source_filename": row[1],
        "project_name": row[2],
        "city": row[3],
        "state": row[4],
        "county": row[5],
        "street_address": row[6],
        "total_sqft": float(row[7]) if row[7] is not None else None,
        "total_acres": float(row[8]) if row[8] is not None else None,
        "zoning": row[9],
        "unit_count": row[10],
        "lot_count": row[11],
        "estimated_total_value": float(row[12]) if row[12] is not None else None,
        "estimated_per_lot_value": float(row[13]) if row[13] is not None else None,
        "has_utility_easement": row[14],
        "easement_description": row[15],
        "setback_notes": row[16],
        "ai_summary": row[17],
        "analysis": {
            "model": row[19],
            "cost_usd": float(row[20]) if row[20] is not None else None,
            "tokens_in": row[21],
            "tokens_out": row[22],
        },
        "status": row[23],
        "created_at": row[24].isoformat() if row[24] else None,
        "lots": [
            {
                "id": str(l[0]),
                "lot_label": l[1],
                "side": l[2],
                "width_ft": float(l[3]) if l[3] is not None else None,
                "depth_ft": float(l[4]) if l[4] is not None else None,
                "lot_sqft": float(l[5]) if l[5] is not None else None,
                "structure_footprint_sqft": float(l[6]) if l[6] is not None else None,
                "structure_type": l[7],
                "retail_price_usd": float(l[8]) if l[8] is not None else None,
                "pricing_note": l[9],
                "is_price_override": l[10],
                "within_easement_buffer": l[11],
            }
            for l in lots
        ],
        "scenarios": [
            {
                "id": str(s[0]),
                "scenario_label": s[1],
                "description": s[2],
                "total_sqft": float(s[3]) if s[3] is not None else None,
                "lot_count": s[4],
                "unit_count": s[5],
                "gross_retail_value_usd": float(s[6]) if s[6] is not None else None,
                "per_unit_value_usd": float(s[7]) if s[7] is not None else None,
                "notes": s[8],
            }
            for s in scenarios
        ],
    }


# === SITE_PLANS_ROUTES_END ===


# ═════════════════════════════════════════════════════════════════════
# CROSS-CHILD-DRAFT (CCD): Sophia↔Ophelia messaging endpoints
# Patch 3-a v2 — endpoints + DB + audit (audit calls take cur, defensively wrapped)
# ═════════════════════════════════════════════════════════════════════

_CCD_DEFAULT_INITIATOR_ID = "8892125a-0d06-4544-a4bf-4278ac1b1360"

_CCD_CHILD_BOTS = {
    "sophia": "SOPHIA_BOT_TOKEN",
    "ophelia": "OPHELIA_BOT_TOKEN",
    "hypatia": "HYPATIA_BOT_TOKEN",
}


def _ccd_safe_audit(cur, **kwargs):
    """Audit log with try/except — never crash the endpoint on logging failure."""
    try:
        write_audit_log(cur, **kwargs)
    except Exception as e:
        print(f"[ccd] audit_log warning (non-fatal): {e}")


def _ccd_lookup_target_telegram_id(target_user_id: str) -> str | None:
    if not target_user_id:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT telegram_user_id FROM users WHERE id = %s::uuid",
                    (target_user_id,),
                )
                row = cur.fetchone()
                if row and row[0]:
                    return str(row[0])
    except Exception as e:
        print(f"[ccd] lookup_target_telegram_id error: {e}")
    return None


def _ccd_resolve_target_user_id(target_human_name: str | None) -> str | None:
    if not target_human_name:
        return None
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM users WHERE full_name ILIKE %s LIMIT 1",
                    (f"%{target_human_name}%",),
                )
                row = cur.fetchone()
                if row:
                    return str(row[0])
    except Exception as e:
        print(f"[ccd] resolve_target_user_id error: {e}")
    return None


async def _ccd_send_outbound(child: str, target_telegram_id: str, text: str) -> tuple[bool, str | None]:
    if child not in _CCD_CHILD_BOTS:
        return False, f"unknown child: {child}"
    try:
        if child == "sophia":
            await send_sophia_message(int(target_telegram_id), text)
        elif child == "ophelia":
            await send_ophelia_message(int(target_telegram_id), text)
        elif child == "hypatia":
            await send_hypatia_message(int(target_telegram_id), text)
        return True, None
    except Exception as e:
        return False, f"send_failed: {e}"


# ─────────────────────────────────────────────────────────────────────
# POST /sophia/draft-message
# ─────────────────────────────────────────────────────────────────────
@app.post("/sophia/draft-message")
async def ccd_draft_message(req: DraftMessageRequest):
    initiating_user_id = req.initiating_user_id or _CCD_DEFAULT_INITIATOR_ID

    draft = await draft_cross_child_message(req.intent, req.operational_context)
    intent_type = draft.get("intent_type")

    # Block / error short-circuit
    if intent_type in ("blocked", "error"):
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    _ccd_safe_audit(
                        cur,
                        user_id=initiating_user_id,
                        action_type="cross_child.draft_blocked" if intent_type == "blocked" else "cross_child.draft_error",
                        object_type="cross_child_draft",
                        object_id=None,
                        recommendation_summary=draft.get("blocked_reason") or draft.get("error"),
                        human_decision=None,
                        execution_status="blocked" if intent_type == "blocked" else "failed",
                        error_message=draft.get("notes_for_iosif") or draft.get("raw_response"),
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ccd] block-path audit error (non-fatal): {e}")
        return {"status": intent_type, "draft": draft}

    target_user_id = _ccd_resolve_target_user_id(draft.get("target_human"))

    # INSERT + audit in one transaction
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO cross_child_drafts (
                        initiating_user_id, intent_text, intent_parsed,
                        from_child, to_child, target_human_user_id,
                        ai_to_ai_message, outbound_message, status
                    ) VALUES (
                        %s::uuid, %s, %s::jsonb,
                        %s, %s, %s,
                        %s, %s, 'pending_approval'
                    )
                    RETURNING id, status, created_at
                    """,
                    (
                        initiating_user_id,
                        req.intent,
                        __import__("json").dumps(draft),
                        draft.get("from_child", "sophia"),
                        draft.get("to_child", "ophelia"),
                        target_user_id,
                        draft.get("ai_to_ai_message", ""),
                        draft.get("outbound_message", ""),
                    ),
                )
                row = cur.fetchone()
                draft_id = str(row[0])
                created_at = row[2]

                _ccd_safe_audit(
                    cur,
                    user_id=initiating_user_id,
                    action_type="cross_child.draft_requested",
                    object_type="cross_child_draft",
                    object_id=draft_id,
                    recommendation_summary=draft.get("context_summary"),
                    human_decision=None,
                    execution_status="success",
                    error_message=None,
                )
                conn.commit()
    except Exception as e:
        print(f"[ccd] draft-message INSERT error: {e}")
        return {"status": "error", "error": f"db_insert_failed: {e}"}

    print(f"[ccd] draft created: id={draft_id} from={draft.get('from_child')} to={draft.get('to_child')} target={draft.get('target_human')}")

    return {
        "status": "pending_approval",
        "draft_id": draft_id,
        "created_at": str(created_at),
        "draft": draft,
    }


# ─────────────────────────────────────────────────────────────────────
# POST /sophia/approve-draft/{draft_id}
# ─────────────────────────────────────────────────────────────────────
@app.post("/sophia/approve-draft/{draft_id}")
async def ccd_approve_draft(draft_id: str):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT initiating_user_id, from_child, to_child,
                           target_human_user_id, ai_to_ai_message, outbound_message,
                           status
                    FROM cross_child_drafts
                    WHERE id = %s::uuid
                    """,
                    (draft_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"status": "error", "error": "draft_not_found"}
                (initiating_user_id, from_child, to_child, target_user_id,
                 ai_to_ai_message, outbound_message, current_status) = row
    except Exception as e:
        return {"status": "error", "error": f"db_fetch_failed: {e}"}

    if current_status not in ("pending_approval", "edited"):
        return {"status": "error", "error": "draft_not_approvable", "current_status": current_status}

    target_tg_id = _ccd_lookup_target_telegram_id(str(target_user_id)) if target_user_id else None
    if not target_tg_id:
        return {
            "status": "error",
            "error": "target_telegram_id_not_found",
            "target_user_id": str(target_user_id) if target_user_id else None,
        }

    ok, send_err = await _ccd_send_outbound(to_child, target_tg_id, outbound_message)

    if not ok:
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE cross_child_drafts SET status='failed', failure_reason=%s WHERE id=%s::uuid",
                        (send_err, draft_id),
                    )
                    _ccd_safe_audit(
                        cur,
                        user_id=str(initiating_user_id),
                        action_type="cross_child.outbound_failed",
                        object_type="cross_child_draft",
                        object_id=draft_id,
                        recommendation_summary=None,
                        human_decision="approved",
                        execution_status="failed",
                        error_message=send_err,
                    )
                    conn.commit()
        except Exception as e:
            print(f"[ccd] failure-path db error (non-fatal): {e}")
        return {"status": "error", "error": send_err}

    # Mark sent + audit in one transaction
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cross_child_drafts
                    SET status='sent', approved_at=NOW(), sent_at=NOW()
                    WHERE id = %s::uuid
                    RETURNING sent_at
                    """,
                    (draft_id,),
                )
                row = cur.fetchone()
                sent_at = row[0] if row else None

                _ccd_safe_audit(
                    cur,
                    user_id=str(initiating_user_id),
                    action_type="cross_child.handoff_logged",
                    object_type="cross_child_draft",
                    object_id=draft_id,
                    recommendation_summary=ai_to_ai_message[:500] if ai_to_ai_message else None,
                    human_decision="approved",
                    execution_status="success",
                    error_message=None,
                )
                _ccd_safe_audit(
                    cur,
                    user_id=str(initiating_user_id),
                    action_type="cross_child.outbound_sent",
                    object_type="cross_child_draft",
                    object_id=draft_id,
                    recommendation_summary=outbound_message[:500] if outbound_message else None,
                    human_decision="approved",
                    execution_status="success",
                    error_message=None,
                )
                conn.commit()
    except Exception as e:
        return {"status": "error", "error": f"db_update_failed: {e}"}

    print(f"[ccd] approved + sent: id={draft_id} to_child={to_child} target_tg={target_tg_id}")

    return {
        "status": "sent",
        "draft_id": draft_id,
        "sent_at": str(sent_at),
        "to_child": to_child,
    }


# ─────────────────────────────────────────────────────────────────────
# POST /sophia/edit-draft/{draft_id}
# ─────────────────────────────────────────────────────────────────────
@app.post("/sophia/edit-draft/{draft_id}")
async def ccd_edit_draft(draft_id: str, req: EditDraftRequest):
    if not req.ai_to_ai_message and not req.outbound_message:
        return {"status": "error", "error": "no_changes_provided"}

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                fields = []
                values = []
                if req.ai_to_ai_message is not None:
                    fields.append("ai_to_ai_message = %s")
                    values.append(req.ai_to_ai_message)
                if req.outbound_message is not None:
                    fields.append("outbound_message = %s")
                    values.append(req.outbound_message)
                fields.append("status = 'edited'")
                values.append(draft_id)

                cur.execute(
                    f"""
                    UPDATE cross_child_drafts
                    SET {", ".join(fields)}
                    WHERE id = %s::uuid
                    RETURNING id, status, initiating_user_id, ai_to_ai_message, outbound_message
                    """,
                    tuple(values),
                )
                row = cur.fetchone()
                if not row:
                    return {"status": "error", "error": "draft_not_found"}
                _, status, initiating_user_id, ai_to_ai_message, outbound_message = row

                _ccd_safe_audit(
                    cur,
                    user_id=str(initiating_user_id),
                    action_type="cross_child.draft_edited",
                    object_type="cross_child_draft",
                    object_id=draft_id,
                    recommendation_summary=None,
                    human_decision="edited",
                    execution_status="success",
                    error_message=None,
                )
                conn.commit()
    except Exception as e:
        return {"status": "error", "error": f"db_update_failed: {e}"}

    print(f"[ccd] draft edited: id={draft_id}")

    return {
        "status": status,
        "draft_id": draft_id,
        "ai_to_ai_message": ai_to_ai_message,
        "outbound_message": outbound_message,
    }


# ─────────────────────────────────────────────────────────────────────
# POST /sophia/skip-draft/{draft_id}
# ─────────────────────────────────────────────────────────────────────
@app.post("/sophia/skip-draft/{draft_id}")
async def ccd_skip_draft(draft_id: str):
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE cross_child_drafts
                    SET status = 'skipped'
                    WHERE id = %s::uuid AND status IN ('pending_approval', 'edited')
                    RETURNING id, initiating_user_id
                    """,
                    (draft_id,),
                )
                row = cur.fetchone()
                if not row:
                    return {"status": "error", "error": "draft_not_found_or_not_skippable"}
                _, initiating_user_id = row

                _ccd_safe_audit(
                    cur,
                    user_id=str(initiating_user_id),
                    action_type="cross_child.draft_skipped",
                    object_type="cross_child_draft",
                    object_id=draft_id,
                    recommendation_summary=None,
                    human_decision="skipped",
                    execution_status="success",
                    error_message=None,
                )
                conn.commit()
    except Exception as e:
        return {"status": "error", "error": f"db_update_failed: {e}"}

    print(f"[ccd] draft skipped: id={draft_id}")

    return {"status": "skipped", "draft_id": draft_id}

# ═════════════════════════════════════════════════════════════════════
# END CROSS-CHILD-DRAFT (CCD) endpoints
# ═════════════════════════════════════════════════════════════════════

