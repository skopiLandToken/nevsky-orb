import os
import time
import imaplib
import email
from email.header import decode_header
import psycopg
import httpx
from datetime import datetime, timezone

POLL_INTERVAL = 30

def get_db_connection():
    return psycopg.connect(
        host=os.getenv("POSTGRES_HOST", "postgres"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "nevsky_dev"),
        user=os.getenv("POSTGRES_USER", "nevsky"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )

def send_telegram_message(chat_id: str, text: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("WARN: TELEGRAM_BOT_TOKEN not set")
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        resp = httpx.post(url, json={"chat_id": chat_id, "text": text}, timeout=10)
        data = resp.json()
        if not data.get("ok"):
            print(f"WARN: Telegram send failed: {data}")
    except Exception as e:
        print(f"ERROR: Telegram send exception: {e}")

def fire_due_reminders():
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT r.id, r.title, r.user_id, u.telegram_user_id
                    FROM reminders r
                    JOIN users u ON u.id = r.user_id
                    WHERE r.status = 'pending'
                      AND r.next_trigger_at <= NOW()
                    ORDER BY r.next_trigger_at ASC
                    LIMIT 50
                """)
                due = cur.fetchall()

                if not due:
                    return

                for reminder_id, title, user_id, telegram_user_id in due:
                    print(f"INFO: Firing reminder {reminder_id} for user {user_id}")
                    if telegram_user_id:
                        send_telegram_message(
                            chat_id=telegram_user_id,
                            text=f"Reminder: {title}",
                        )
                    else:
                        print(f"WARN: No telegram_user_id for user {user_id}, skipping notify")

                    cur.execute("""
                        UPDATE reminders
                        SET status = 'fired',
                            updated_at = NOW()
                        WHERE id = %s
                    """, (reminder_id,))

                print(f"INFO: Fired {len(due)} reminder(s) at {datetime.now(timezone.utc).isoformat()}")
        conn.close()

    except Exception as e:
        print(f"ERROR: fire_due_reminders failed: {e}")

def decode_mime_header(value: str) -> str:
    parts = decode_header(value)
    decoded = []
    for part, enc in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)

def get_email_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if ct == "text/plain" and "attachment" not in cd:
                try:
                    body = part.get_payload(decode=True).decode(
                        part.get_content_charset() or "utf-8", errors="replace"
                    )
                    break
                except Exception:
                    pass
    else:
        try:
            body = msg.get_payload(decode=True).decode(
                msg.get_content_charset() or "utf-8", errors="replace"
            )
        except Exception:
            pass
    return body.strip()[:8000]

def is_email_already_processed(message_id: str) -> bool:
    if not message_id:
        return False
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM processed_emails WHERE message_id = %s LIMIT 1",
                    (message_id,)
                )
                return cur.fetchone() is not None
        conn.close()
    except Exception as e:
        print(f"ERROR: is_email_already_processed failed: {e}")
        return False

def mark_email_processed(message_id: str, sender_email: str, subject: str):
    if not message_id:
        return
    try:
        conn = get_db_connection()
        with conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO processed_emails (message_id, sender_email, subject)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (message_id) DO NOTHING
                    """,
                    (message_id, sender_email, subject)
                )
        conn.close()
    except Exception as e:
        print(f"ERROR: mark_email_processed failed: {e}")

def poll_imap():
    host = os.getenv("IMAP_HOST", "")
    port = int(os.getenv("IMAP_PORT", 993))
    user = os.getenv("IMAP_USER", "")
    password = os.getenv("IMAP_PASSWORD", "")

    if not host or not user or not password:
        print("WARN: IMAP not configured, skipping")
        return

    try:
        mail = imaplib.IMAP4_SSL(host, port)
        mail.login(user, password)
        mail.select("INBOX")

        status, messages = mail.search(None, "ALL")
        if not messages[0]:
            mail.logout()
            return

        msg_ids = messages[0].split()
        print(f"INFO: Found {len(msg_ids)} email(s) in INBOX")

        for msg_id in msg_ids:
            try:
                status, data = mail.fetch(msg_id, "(RFC822)")
                raw = data[0][1]
                msg = email.message_from_bytes(raw)

                subject = decode_mime_header(msg.get("Subject", "(no subject)"))
                sender = msg.get("From", "unknown")
                thread_id = msg.get("Message-ID", "").strip()
                body = get_email_body(msg)

                if is_email_already_processed(thread_id):
                    continue

                print(f"INFO: Processing email '{subject}' from {sender}")

                payload = {
                    "owner_email": "iosif@skopi.io",
                    "sender_email": sender,
                    "subject": subject,
                    "body": body,
                    "thread_id": thread_id or None,
                }

                resp = httpx.post(
                    "http://api:8080/ingest/email/test",
                    json=payload,
                    timeout=30,
                )
                result = resp.json()

                if result.get("ok") or result.get("filtered"):
                    mark_email_processed(thread_id, sender, subject)
                    mail.store(msg_id, "+FLAGS", "\\Seen")
                    print(f"INFO: Email processed and marked read thread {thread_id}")
                else:
                    print(f"WARN: Ingest returned not-ok: {result}")

            except Exception as e:
                print(f"ERROR: Failed to process email {msg_id}: {e}")

        mail.logout()

    except Exception as e:
        print(f"ERROR: poll_imap failed: {e}")

print("Nevsky worker started")
print("Environment:", os.getenv("ENVIRONMENT", "unknown"))
print(f"Poll interval: {POLL_INTERVAL}s")

while True:
    fire_due_reminders()
    poll_imap()
    time.sleep(POLL_INTERVAL)
