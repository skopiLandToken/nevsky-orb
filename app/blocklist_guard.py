"""
Blocklist guard — universal entry-point check for all ORB child webhooks.
If a user is on the blocklist, log + archive + alert Iosif, then return True
so the caller short-circuits without invoking the LLM.

Doctrine: locked April 27, 2026. Honeypot mode for adversarial actors.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path
import httpx
import psycopg
from psycopg.rows import dict_row

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")
SOPHIA_BOT_TOKEN = os.environ.get("SOPHIA_BOT_TOKEN", "")
IOSIF_TELEGRAM_ID = os.environ.get("IOSIF_TELEGRAM_ID", "")
ARCHIVE_PATH = Path("/opt/nevsky-dev/kb/Archives/fred_jewell_archive/evidence_documentation/contact_attempts.jsonl")


def _get_db_conn():
    return psycopg.connect(DB_DSN, row_factory=dict_row)


def is_blocked(telegram_user_id: int) -> dict | None:
    """Return blocklist row dict if user is blocked, else None."""
    try:
        with _get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, telegram_user_id, block_reason, block_severity, notes FROM personas_blocklist WHERE telegram_user_id = %s",
                    (telegram_user_id,),
                )
                return cur.fetchone()
    except Exception as e:
        print(f"[blocklist_guard] DB error checking blocklist: {e}")
        return None


def log_contact_attempt(bot_name: str, telegram_user_id: int, chat_id: int, message_text: str, message_type: str, full_update: dict) -> int | None:
    """Insert a row into fred_contact_log + append to JSONL archive. Return row id."""
    archive_record = {
        "received_at": datetime.now(timezone.utc).isoformat(),
        "bot_name": bot_name,
        "telegram_user_id": telegram_user_id,
        "chat_id": chat_id,
        "message_text": message_text,
        "message_type": message_type,
        "full_update": full_update,
    }
    try:
        ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with ARCHIVE_PATH.open("a") as f:
            f.write(json.dumps(archive_record) + "\n")
    except Exception as e:
        print(f"[blocklist_guard] Failed to write JSONL archive: {e}")

    try:
        with _get_db_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO fred_contact_log
                       (bot_name, telegram_user_id, chat_id, message_text, message_type, full_update_json, archive_file_path)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       RETURNING id""",
                    (bot_name, telegram_user_id, chat_id, message_text, message_type, json.dumps(full_update), str(ARCHIVE_PATH)),
                )
                row = cur.fetchone()
                conn.commit()
                return row["id"] if row else None
    except Exception as e:
        print(f"[blocklist_guard] Failed to insert contact log row: {e}")
        return None


async def alert_iosif_via_sophia(bot_name: str, telegram_user_id: int, message_text: str, contact_log_id: int | None) -> bool:
    """Send immediate Telegram alert to Iosif via Sophia bot."""
    if not SOPHIA_BOT_TOKEN or not IOSIF_TELEGRAM_ID:
        print("[blocklist_guard] Missing SOPHIA_BOT_TOKEN or IOSIF_TELEGRAM_ID — cannot alert")
        return False

    alert_text = (
        f"🚨 BLOCKLIST CONTACT ATTEMPT\n\n"
        f"Bot: {bot_name}\n"
        f"From Telegram ID: {telegram_user_id}\n"
        f"Contact Log ID: {contact_log_id}\n\n"
        f"Message:\n{message_text[:1000]}"
    )
    url = f"https://api.telegram.org/bot{SOPHIA_BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json={"chat_id": IOSIF_TELEGRAM_ID, "text": alert_text})
            ok = resp.status_code == 200
            if ok and contact_log_id:
                with _get_db_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE fred_contact_log SET alert_sent = TRUE, alert_sent_at = NOW() WHERE id = %s",
                            (contact_log_id,),
                        )
                        conn.commit()
            return ok
    except Exception as e:
        print(f"[blocklist_guard] Failed to send Sophia alert: {e}")
        return False


async def check_blocklist_and_archive(telegram_user_id: int, chat_id: int, message_text: str, bot_name: str, message_type: str = "text", full_update: dict | None = None) -> bool:
    """
    Universal entry-point check. Call from every bot webhook BEFORE any LLM logic.
    Returns True if blocked (caller should return early). Returns False if allowed.
    """
    block_row = is_blocked(telegram_user_id)
    if not block_row:
        return False

    contact_log_id = log_contact_attempt(
        bot_name=bot_name,
        telegram_user_id=telegram_user_id,
        chat_id=chat_id,
        message_text=message_text or "",
        message_type=message_type,
        full_update=full_update or {},
    )
    await alert_iosif_via_sophia(
        bot_name=bot_name,
        telegram_user_id=telegram_user_id,
        message_text=message_text or "",
        contact_log_id=contact_log_id,
    )
    print(f"[blocklist_guard] BLOCKED contact from {telegram_user_id} to {bot_name} — log id {contact_log_id}")
    return True
