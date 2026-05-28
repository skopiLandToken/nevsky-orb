"""
email_client.py — Outbound email via Resend HTTP API + webhook ingestion.

DOCTRINE-OUTBOUND-EMAIL-01 (locked 2026-05-28):
  All outbound mail from Nevsky/SKOpi infrastructure routes through the Resend
  HTTP API. SMTP from droplets is permanently bypassed (DigitalOcean blocks the
  ports, and HTTP is the right transport for transactional mail regardless).

Three-key separation (Iosif locked 2026-05-28): one Resend API key per send
purpose, so audit + revocation surfaces stay clean (six-locks ethos). Routing is
by `send_category`; the default is "nevsky" (the safest, internal/ORB key).

  nevsky        -> RESEND_API_KEY_NEVSKY_PROD   transactional/ORB-driven internal
  marketing     -> RESEND_API_KEY_MARKETING     MP outreach, drip, recruitment
  transactional -> RESEND_API_KEY_TRANSACTIONAL customer receipts, signups, resets

We deliberately stay on httpx rather than pulling in the `resend` SDK: httpx is
already a dependency, the existing send path used it, and webhook verification is
hand-rolled HMAC anyway (the brief prefers explicit HMAC over a trust-me helper),
so the SDK would only add surface for no gain.
"""

import os
import json
import hmac
import base64
import hashlib
import logging

import httpx

logger = logging.getLogger("email_client")

RESEND_KEY_CATEGORIES = {"nevsky", "marketing", "transactional"}

RESEND_API_URL = "https://api.resend.com/emails"


def _key_for(send_category: str) -> str | None:
    """Resolve the Resend API key for a send category.

    nevsky falls back to the legacy single RESEND_API_KEY *only* — that key was
    already the internal/ORB sender, so this preserves the live approval flow if
    container env reload lags behind .env. marketing/transactional have NO
    fallback on purpose: routing those through the internal key would defeat the
    separation Iosif locked, so a missing key must fail loud.
    """
    if send_category == "nevsky":
        return os.getenv("RESEND_API_KEY_NEVSKY_PROD") or os.getenv("RESEND_API_KEY")
    if send_category == "marketing":
        return os.getenv("RESEND_API_KEY_MARKETING")
    if send_category == "transactional":
        return os.getenv("RESEND_API_KEY_TRANSACTIONAL")
    return None


def send_email_via_resend(
    to,
    subject: str,
    html: str | None = None,
    text: str | None = None,
    from_addr: str = "iosif@skopi.io",
    reply_to: str | None = None,
    headers: dict | None = None,
    tags: list | None = None,
    send_category: str = "nevsky",
) -> dict:
    """Send outbound email via the Resend HTTP API.

    Returns the same dict shape the legacy send_email_via_smtp used so existing
    callers need no changes: {'ok': bool, 'to', 'subject', 'resend_id'|'error'}.
    """
    if send_category not in RESEND_KEY_CATEGORIES:
        return {"ok": False, "error": f"Unknown send_category {send_category!r}"}

    key = _key_for(send_category)
    if not key:
        return {"ok": False, "error": f"No Resend key configured for category {send_category!r}"}

    payload: dict = {
        "from": from_addr,
        "to": [to] if isinstance(to, str) else list(to),
        "subject": subject,
    }
    if html:
        payload["html"] = html
    if text:
        payload["text"] = text
    if reply_to:
        payload["reply_to"] = reply_to
    if headers:
        payload["headers"] = headers
    if tags:
        payload["tags"] = tags  # [{"name": "draft_id", "value": "..."}], values must be [A-Za-z0-9_-]

    try:
        resp = httpx.post(
            RESEND_API_URL,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=15,
        )
        if resp.status_code in (200, 201):
            return {
                "ok": True,
                "to": payload["to"],
                "subject": subject,
                "send_category": send_category,
                "resend_id": resp.json().get("id"),
            }
        return {"ok": False, "error": f"Resend API error {resp.status_code}: {resp.text}"}
    except Exception as e:  # noqa: BLE001 — surface the transport error to the caller verbatim
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Webhook signature verification (Svix scheme — Resend signs via Svix)
# ─────────────────────────────────────────────────────────────────────────────
def verify_resend_webhook(headers: dict, raw_body: bytes) -> bool:
    """Verify a Resend (Svix) webhook signature with explicit HMAC-SHA256.

    Signed content is `{svix-id}.{svix-timestamp}.{raw_body}`. The secret is
    `whsec_<base64>`; we strip the prefix, base64-decode the remainder to the raw
    HMAC key, and constant-time-compare our computed signature against each
    `v1,<sig>` entry in the (space-delimited, possibly multi-version) svix-signature
    header. Fail closed on any missing piece.
    """
    secret = os.getenv("RESEND_WEBHOOK_SECRET", "")
    if not secret:
        logger.error("RESEND_WEBHOOK_SECRET not configured — rejecting webhook")
        return False

    # Header lookups are case-insensitive in practice; normalize.
    h = {k.lower(): v for k, v in headers.items()}
    svix_id = h.get("svix-id")
    svix_ts = h.get("svix-timestamp")
    svix_sig = h.get("svix-signature")
    if not (svix_id and svix_ts and svix_sig):
        logger.warning("webhook missing svix-id/timestamp/signature headers")
        return False

    key_b64 = secret.split("_", 1)[1] if secret.startswith("whsec_") else secret
    try:
        key = base64.b64decode(key_b64)
    except Exception:
        logger.error("RESEND_WEBHOOK_SECRET is not valid base64")
        return False

    signed_content = b".".join([svix_id.encode(), svix_ts.encode(), raw_body])
    expected = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode()

    # svix-signature: "v1,sig1 v1,sig2" — compare against every v1 entry.
    for part in svix_sig.split():
        version, _, sig = part.partition(",")
        if version == "v1" and hmac.compare_digest(sig, expected):
            return True
    logger.warning("webhook signature mismatch")
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Event ingestion: raw -> email_events, derived aggregate -> knowledge_store
# ─────────────────────────────────────────────────────────────────────────────
IOSIF_USER_ID = "8892125a-0d06-4544-a4bf-4278ac1b1360"


def _extract_tag(data: dict, name: str) -> str | None:
    """Pull a tag value out of a Resend event payload. Resend returns tags either
    as a list of {name,value} dicts or as a flat {name: value} object depending on
    surface — handle both."""
    tags = data.get("tags")
    if isinstance(tags, dict):
        v = tags.get(name)
        return str(v) if v is not None else None
    if isinstance(tags, list):
        for t in tags:
            if isinstance(t, dict) and t.get("name") == name:
                return t.get("value")
    return None


def ingest_email_event(event: dict, get_db_connection) -> dict:
    """Persist one Resend webhook event and refresh its derived KS telemetry row.

    `get_db_connection` is injected (rather than imported) to avoid a circular
    import with main.py. Idempotent: the raw insert uses ON CONFLICT DO NOTHING
    against the (message_id, event_type, occurred_at) dedupe index, and the KS
    row is upserted by title.
    """
    event_type = event.get("type", "")
    data = event.get("data", {}) or {}

    message_id = data.get("email_id") or data.get("id") or ""
    recipient = data.get("to")
    if isinstance(recipient, list):
        recipient = recipient[0] if recipient else None
    occurred_at = event.get("created_at") or data.get("created_at")
    send_category = _extract_tag(data, "send_category")
    draft_id = _extract_tag(data, "draft_id")

    if not message_id:
        logger.warning("email event has no message id; skipping (type=%s)", event_type)
        return {"ok": False, "error": "no message id"}

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO email_events
                    (resend_message_id, event_type, event_data, send_category,
                     draft_id, recipient_email, occurred_at)
                VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
                ON CONFLICT (resend_message_id, event_type, occurred_at) DO NOTHING
                """,
                (message_id, event_type, json.dumps(event), send_category,
                 draft_id, recipient, occurred_at),
            )
            conn.commit()
        _refresh_ks_telemetry(conn, message_id)

    return {"ok": True, "message_id": message_id, "event_type": event_type}


# event_type -> "this is the final disposition" ranking. Highest wins.
_TERMINAL_RANK = {
    "email.sent": 1,
    "email.delivery_delayed": 2,
    "email.delivered": 3,
    "email.opened": 3,
    "email.clicked": 3,
    "email.complained": 4,
    "email.bounced": 5,
    "email.failed": 5,
}


def _refresh_ks_telemetry(conn, message_id: str) -> None:
    """Recompute the founder-private aggregate row for one message and upsert it.

    DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01: email metadata leaks who/when/how-often
    Iosif communicates, so the row is is_private=true with NO executive_readable
    tag. Sophia (sovereign) reads it for diagnostics; executive tier sees nothing
    here without an explicit Iosif approval card.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT event_type, event_data, send_category, draft_id,
                   recipient_email, occurred_at
            FROM email_events
            WHERE resend_message_id = %s
            ORDER BY occurred_at ASC
            """,
            (message_id,),
        )
        rows = cur.fetchall()
        if not rows:
            return

        opens = sum(1 for r in rows if r[0] == "email.opened")
        clicks = sum(1 for r in rows if r[0] == "email.clicked")
        types = [r[0] for r in rows]
        final_status = max(types, key=lambda t: _TERMINAL_RANK.get(t, 0))
        send_category = next((r[2] for r in rows if r[2]), None)
        draft_id = next((r[3] for r in rows if r[3]), None)
        recipient = next((r[4] for r in rows if r[4]), None)
        subject = None
        for r in rows:
            subject = (r[1] or {}).get("data", {}).get("subject") if isinstance(r[1], dict) else None
            if subject:
                break

        summary = {
            "resend_message_id": message_id,
            "recipient": recipient,
            "subject": subject,
            "send_category": send_category,
            "draft_id": draft_id,
            "final_status": final_status,
            "opens": opens,
            "clicks": clicks,
            "event_count": len(rows),
            "lifecycle": types,
        }
        title = f"email_event_{message_id}"
        content = json.dumps(summary, indent=2)
        tags = ["email", "telemetry"] + ([send_category] if send_category else [])

        cur.execute(
            "SELECT id FROM knowledge_store WHERE title = %s AND source_type = 'email_telemetry' LIMIT 1",
            (title,),
        )
        existing = cur.fetchone()
        if existing:
            cur.execute(
                """
                UPDATE knowledge_store
                SET content = %s, tags = %s, metadata = %s::jsonb, updated_at = NOW()
                WHERE id = %s
                """,
                (content, tags, json.dumps(summary), existing[0]),
            )
        else:
            cur.execute(
                """
                INSERT INTO knowledge_store
                    (title, content, source, source_type, content_type, tags,
                     metadata, is_private, owner_user_id)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
                """,
                (title, content, "resend_webhook", "email_telemetry", "email_telemetry",
                 tags, json.dumps(summary), True, IOSIF_USER_ID),
            )
        conn.commit()
