from fastapi import FastAPI, Request
from pydantic import BaseModel
import os
from datetime import datetime, timezone
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
    text = message.get("text")
    update_id = payload.get("update_id")

    summary = f"Telegram update {update_id}"
    if text:
        summary = f"Telegram message: {text[:200]}"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO events (
                    tenant_id,
                    source_type,
                    event_type,
                    priority,
                    approval_required,
                    summary,
                    normalized_payload_json,
                    occurred_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    "skopi",
                    "telegram",
                    "message.telegram.received",
                    "normal",
                    False,
                    summary,
                    __import__("json").dumps(payload),
                    datetime.now(timezone.utc),
                ),
            )
            event_id = cur.fetchone()[0]
        conn.commit()

    return {
        "ok": True,
        "source": "telegram",
        "event_id": str(event_id),
        "received_at": datetime.now(timezone.utc).isoformat(),
        "top_level_keys": list(payload.keys()),
    }
