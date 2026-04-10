from fastapi import FastAPI, Request
from pydantic import BaseModel
import os
import json
from datetime import datetime, timezone

app = FastAPI(title="Nevsky API", version="0.1.0")

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
    return {
        "ok": True,
        "source": "telegram",
        "received_at": datetime.now(timezone.utc).isoformat(),
        "top_level_keys": list(payload.keys()),
    }
