"""
email_webhook.py — Resend webhook receiver (POST /webhook/resend).

Kept as its own APIRouter (rather than another @app.post in main.py) because
main.py is a high-contention shared file across concurrent Yindo sessions; a
sibling router means one include line in main.py and zero merge risk on the
endpoint body itself.

Resend dashboard config: webhook URL = https://nevsky.skopi.io/webhook/resend
Subscribed events: email.sent, email.delivered, email.delivery_delayed,
email.bounced, email.complained, email.opened, email.clicked, email.failed.
"""

import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from email_client import verify_resend_webhook, ingest_email_event

logger = logging.getLogger("email_webhook")

router = APIRouter()


@router.post("/webhook/resend", include_in_schema=False)
async def resend_webhook(request: Request):
    # Raw body is required for signature verification — read it before any parse.
    raw = await request.body()

    if not verify_resend_webhook(dict(request.headers), raw):
        return JSONResponse(status_code=401, content={"ok": False, "error": "invalid signature"})

    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning("resend webhook: bad JSON body: %s", e)
        return JSONResponse(status_code=400, content={"ok": False, "error": "bad json"})

    # Ingestion is cheap (two indexed writes); do it inline and still return fast.
    # get_db_connection is injected to avoid a circular import with main.py.
    try:
        from main import get_db_connection
        result = ingest_email_event(event, get_db_connection)
    except Exception as e:  # noqa: BLE001 — never 500 a webhook; Svix would retry-storm us
        logger.error("resend webhook ingest failed: %s", e)
        return JSONResponse(status_code=200, content={"ok": False, "error": "ingest failed (logged)"})

    return {"ok": True, "ingested": result}
