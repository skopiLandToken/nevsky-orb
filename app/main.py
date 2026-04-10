from fastapi import FastAPI, Request
from pydantic import BaseModel
import os
import json
from datetime import datetime, timezone, timedelta
import psycopg

app = FastAPI(title="Nevsky API", version="0.1.0")

def get_db_connection():
    return psycopg.connect(
        host="postgres",
        dbname=os.getenv("POSTGRES_DB", "nevsky_dev"),
        user=os.getenv("POSTGRES_USER", "nevsky"),
        password=os.getenv("POSTGRES_PASSWORD", "change_me_now"),
    )

class HealthResponse(BaseModel):
    status: str
    environment: str

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        environment=os.getenv("ENVIRONMENT", "unknown"),
    )

@app.get("/ready")
def ready():
    return {"ready": True}

@app.post("/ingest/telegram-update")
async def ingest_telegram_update(request: Request):
    payload = await request.json()

    message = payload.get("message", {}) or {}
    text = (message.get("text") or "").strip()
    update_id = payload.get("update_id")

    summary = f"Telegram update {update_id}"
    if text:
        summary = f"Telegram message: {text[:200]}"

    task_created = False
    task_id = None
    reminder_created = False
    reminder_id = None
    owner_user_id = None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM users WHERE email = %s LIMIT 1",
                ("iosifskorohodov@gmail.com",),
            )
            row = cur.fetchone()
            if row:
                owner_user_id = row[0]

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

            elif text.lower().startswith("/remind "):
                title = text[8:].strip()
                if title:
                    trigger_at = datetime.now(timezone.utc) + timedelta(hours=1)
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

        conn.commit()

    return {
        "ok": True,
        "source": "telegram",
        "event_id": str(event_id),
        "task_created": task_created,
        "task_id": str(task_id) if task_id else None,
        "reminder_created": reminder_created,
        "reminder_id": str(reminder_id) if reminder_id else None,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "top_level_keys": list(payload.keys()),
    }
