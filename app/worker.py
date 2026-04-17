import os
import time
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
                    print(f"INFO: Firing reminder {reminder_id} — '{title}' for user {user_id}")

                    if telegram_user_id:
                        send_telegram_message(
                            chat_id=telegram_user_id,
                            text=f"🔔 Reminder: {title}",
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

print("Nevsky worker started")
print("Environment:", os.getenv("ENVIRONMENT", "unknown"))
print(f"Poll interval: {POLL_INTERVAL}s")

while True:
    fire_due_reminders()
    time.sleep(POLL_INTERVAL)
