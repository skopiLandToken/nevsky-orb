"""
cpanel.py — GreenGeeks cPanel UAPI client for programmatic mailbox provisioning.

DOCTRINE-MAILBOX-PROVISIONING-01 (Iosif locked 2026-05-28):
  SKOpi mailbox provisioning happens via Nevsky -> GreenGeeks cPanel UAPI using a
  SCOPED API token (Authorization: cpanel <user>:<token>) — never the account
  password. Six-locks / least-privilege: a leaked or stale token is revocable in
  the cPanel UI without rotating the account login.

  Email infrastructure split (locked):
    GreenGeeks  -> mail storage + IMAP receive (warmed deliverability, unchanged)
    Resend      -> all OUTBOUND (DOCTRINE-OUTBOUND-EMAIL-01, see email_client.py)
    Nevsky      -> the CONTROL PLANE: create/list/rotate/delete mailboxes on demand

We deliberately stay on the UAPI (modern JSON `/execute/<Module>/<Function>`) over
the deprecated XML-API, and on httpx over the `cpanel` SDK — httpx is already a
dependency and the auth is a single static header, so an SDK would only add surface.

Two write surfaces, two postures:
  - provision / list / rotate / quota run immediately (Sophia is sovereign = Iosif's
    hands).
  - delete is DESTRUCTIVE, so the Sophia tool does NOT delete inline — it enqueues a
    pending row and a Sophia approval card fires to Iosif (cpanel_pending_deletes).
    The actual delete_mailbox() call runs only from the approve callback in main.py.

EVERY call (success or failure) writes one row to cpanel_operations. The error trail
is the point — a failed provision must leave a record exactly like a successful one.
"""

import os
import json
import string
import secrets
import logging

import httpx
import psycopg

logger = logging.getLogger("cpanel")

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

# UAPI call timeout. cPanel is generally snappy; provision/delete are the slow ones.
_HTTP_TIMEOUT = 20


class CPanelError(Exception):
    """Raised on any cPanel failure (transport, non-200, or UAPI status:0).

    Carries `.response` (the parsed UAPI envelope or an HTTP-error dict) so the
    caller can audit the exact failure payload, not just the message string.
    """

    def __init__(self, message: str, response=None):
        super().__init__(message)
        self.response = response


# ─────────────────────────────────────────────────────────────────────────────
# Config + low-level transport
# ─────────────────────────────────────────────────────────────────────────────
def _config() -> tuple[str, str, str, str]:
    """Resolve (host, username, token, default_domain) from env. Fail loud — a
    missing token must not silently degrade to an unauthenticated call."""
    host = os.getenv("CPANEL_HOST", "")
    user = os.getenv("CPANEL_USERNAME", "")
    token = os.getenv("CPANEL_API_TOKEN", "")
    default_domain = os.getenv("CPANEL_DEFAULT_DOMAIN", "skopi.io")
    missing = [n for n, v in (("CPANEL_HOST", host), ("CPANEL_USERNAME", user),
                              ("CPANEL_API_TOKEN", token)) if not v]
    if missing:
        raise CPanelError(f"cPanel not configured — missing env: {', '.join(missing)}")
    return host, user, token, default_domain


def _conn():
    return psycopg.connect(DB_DSN)


def _split_email(email: str) -> tuple[str, str]:
    """Return (local_part, domain). cPanel's Email UAPI takes the local part in
    `email` and the domain separately. Accept either 'jdoe' or 'jdoe@skopi.io';
    a bare local part defaults to CPANEL_DEFAULT_DOMAIN."""
    _, _, _, default_domain = _config()
    if "@" in email:
        local, domain = email.split("@", 1)
    else:
        local, domain = email, default_domain
    return local.strip(), domain.strip()


def _call(module: str, function: str, params: dict | None = None, method: str = "GET") -> dict:
    """Execute one UAPI call and return the parsed envelope.

    Raises CPanelError on transport failure, non-200, non-JSON, or UAPI status:0.
    Params ride the query string for both GET and POST — UAPI reads them from the
    query string regardless of method, and that keeps the token the only thing in
    the header.
    """
    host, user, token, _ = _config()
    url = f"https://{host}/execute/{module}/{function}"
    headers = {"Authorization": f"cpanel {user}:{token}"}
    try:
        resp = httpx.request(method, url, headers=headers, params=params or {}, timeout=_HTTP_TIMEOUT)
    except Exception as e:  # noqa: BLE001 — surface transport error verbatim
        raise CPanelError(f"cPanel transport error on {module}/{function}: {e}")

    if resp.status_code != 200:
        raise CPanelError(
            f"cPanel HTTP {resp.status_code} on {module}/{function}: {resp.text[:300]}",
            response={"http_status": resp.status_code, "body": resp.text[:1000]},
        )
    try:
        data = resp.json()
    except Exception:
        raise CPanelError(f"cPanel non-JSON response on {module}/{function}: {resp.text[:300]}")

    # UAPI success envelope: {"status": 1, "data": ..., "errors": null, "warnings": ...}
    # Failure: {"status": 0, "errors": ["..."], ...}. Treat any falsy status as failure.
    if not data.get("status"):
        errs = data.get("errors") or ["unknown cPanel error"]
        raise CPanelError(
            f"cPanel {module}/{function} failed: {'; '.join(str(m) for m in errs)}",
            response=data,
        )
    return data


def _audit(operation: str, target_email: str | None, invoked_by: str,
           success: bool, response, error: str | None) -> None:
    """Append one row to cpanel_operations. Never raises into the caller — a broken
    audit write must not mask the real operation result, but it IS logged loudly."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO cpanel_operations
                       (operation, target_email, invoked_by, success, cpanel_response, error_message)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
                (operation, target_email, invoked_by, success,
                 json.dumps(response, default=str) if response is not None else None, error),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("cpanel audit write FAILED (op=%s email=%s): %s", operation, target_email, e)


def generate_password(length: int = 20) -> str:
    """Strong random password with guaranteed upper/lower/digit/symbol diversity —
    cPanel rejects low-strength passwords, so we enforce the mix rather than retry-
    on-reject against the API."""
    symbols = "!@#$%^&*-_=+"
    pool = string.ascii_letters + string.digits + symbols
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in symbols for c in pw)):
            return pw


# ─────────────────────────────────────────────────────────────────────────────
# Core wrappers — raise CPanelError on failure, audit every call
# ─────────────────────────────────────────────────────────────────────────────
def provision_mailbox(email: str, password: str, quota_mb: int = 0, invoked_by: str = "manual") -> dict:
    """Create a mailbox. quota_mb=0 means unlimited. Returns provisioned info."""
    local, domain = _split_email(email)
    full = f"{local}@{domain}"
    try:
        data = _call("Email", "add_pop",
                     {"email": local, "domain": domain, "password": password, "quota": quota_mb},
                     method="POST")
    except CPanelError as e:
        _audit("provision", full, invoked_by, False, e.response, str(e))
        raise
    _audit("provision", full, invoked_by, True, data, None)
    return {"email": full, "quota_mb": quota_mb, "cpanel": data.get("data")}


def list_mailboxes(domain: str | None = None, invoked_by: str = "manual") -> list:
    """List mailboxes on the cPanel account (optionally filtered to one domain)."""
    params = {"domain": domain} if domain else {}
    try:
        data = _call("Email", "list_pops", params, method="GET")
    except CPanelError as e:
        _audit("list", domain, invoked_by, False, e.response, str(e))
        raise
    rows = data.get("data") or []
    mailboxes = [
        {
            "email": r.get("email"),
            "login": r.get("login"),
            "suspended_login": r.get("suspended_login"),
            "suspended_incoming": r.get("suspended_incoming"),
        }
        for r in rows if isinstance(r, dict)
    ]
    _audit("list", domain, invoked_by, True, {"count": len(mailboxes)}, None)
    return mailboxes


def rotate_mailbox_password(email: str, new_password: str, invoked_by: str = "manual") -> dict:
    """Rotate the password for an existing mailbox."""
    local, domain = _split_email(email)
    full = f"{local}@{domain}"
    try:
        data = _call("Email", "passwd_pop",
                     {"email": local, "domain": domain, "password": new_password},
                     method="POST")
    except CPanelError as e:
        _audit("rotate", full, invoked_by, False, e.response, str(e))
        raise
    _audit("rotate", full, invoked_by, True, data, None)
    return {"email": full, "cpanel": data.get("data")}


def delete_mailbox(email: str, invoked_by: str = "manual") -> dict:
    """Delete a mailbox. DESTRUCTIVE — Sophia callers go through the approval queue
    (enqueue_mailbox_delete + the approve callback), never call this inline."""
    local, domain = _split_email(email)
    full = f"{local}@{domain}"
    try:
        data = _call("Email", "delete_pop", {"email": local, "domain": domain}, method="POST")
    except CPanelError as e:
        _audit("delete", full, invoked_by, False, e.response, str(e))
        raise
    _audit("delete", full, invoked_by, True, data, None)
    return {"email": full, "deleted": True, "cpanel": data.get("data")}


def get_mailbox_quota(email: str, invoked_by: str = "manual") -> dict:
    """Get quota usage for a mailbox."""
    local, domain = _split_email(email)
    full = f"{local}@{domain}"
    try:
        data = _call("Email", "get_pop_quota", {"email": local, "domain": domain}, method="GET")
    except CPanelError as e:
        _audit("quota", full, invoked_by, False, e.response, str(e))
        raise
    _audit("quota", full, invoked_by, True, data, None)
    return {"email": full, "quota": data.get("data")}


# ─────────────────────────────────────────────────────────────────────────────
# Delete approval queue (destructive op gate, DOCTRINE-MAILBOX-PROVISIONING-01)
# ─────────────────────────────────────────────────────────────────────────────
def enqueue_mailbox_delete(email: str, requested_by: str = "sophia") -> int:
    """Record a pending delete and return its id. A separate sweeper in main.py
    fires the Sophia approval card to Iosif (card_sent=false => still owed). Dedupes
    against an existing pending row for the same mailbox."""
    local, domain = _split_email(email)
    full = f"{local}@{domain}"
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM cpanel_pending_deletes WHERE target_email=%s AND status='pending' LIMIT 1",
            (full,),
        )
        existing = cur.fetchone()
        if existing:
            return int(existing[0])
        cur.execute(
            "INSERT INTO cpanel_pending_deletes (target_email, requested_by) VALUES (%s, %s) RETURNING id",
            (full, requested_by),
        )
        new_id = int(cur.fetchone()[0])
        conn.commit()
        return new_id


# ─────────────────────────────────────────────────────────────────────────────
# Sophia tool layer (sovereign only — registered in child_engine TIER_TOOLS)
# Return structured dicts (never raise) so the model gets a clean result to narrate.
# Initial/rotated passwords are returned in the tool result for Sophia to relay to
# Iosif via Telegram — they must NEVER be surfaced to a non-founder requester.
# ─────────────────────────────────────────────────────────────────────────────
def sophia_provision_mailbox(email: str, password: str | None = None, quota_mb: int = 0) -> dict:
    """Create a mailbox. If password is omitted, a strong one is generated and
    returned for Iosif (deliver via Telegram, not chat history)."""
    generated = password is None
    pw = password or generate_password()
    try:
        result = provision_mailbox(email, pw, quota_mb=quota_mb, invoked_by="sophia")
    except CPanelError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "email": result["email"],
        "quota_mb": quota_mb if quota_mb else 0,
        "quota_label": "unlimited" if not quota_mb else f"{quota_mb} MB",
        "password": pw,
        "password_generated": generated,
        "note": "Deliver this password to Iosif via Telegram. Do NOT expose it to anyone but the founder.",
    }


def sophia_list_mailboxes(domain: str | None = None) -> dict:
    """List mailboxes on the cPanel account, optionally filtered by domain."""
    try:
        mailboxes = list_mailboxes(domain=domain, invoked_by="sophia")
    except CPanelError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": len(mailboxes), "mailboxes": mailboxes}


def sophia_rotate_mailbox_password(email: str, new_password: str | None = None) -> dict:
    """Rotate a mailbox password. If new_password is omitted, a strong one is
    generated and returned for Iosif."""
    generated = new_password is None
    pw = new_password or generate_password()
    try:
        rotate_mailbox_password(email, pw, invoked_by="sophia")
    except CPanelError as e:
        return {"ok": False, "error": str(e)}
    local, domain = _split_email(email)
    return {
        "ok": True,
        "email": f"{local}@{domain}",
        "password": pw,
        "password_generated": generated,
        "note": "Deliver the new password to Iosif via Telegram. Old password is now invalid.",
    }


def sophia_delete_mailbox(email: str) -> dict:
    """Request deletion of a mailbox. DESTRUCTIVE — this does NOT delete. It enqueues
    a pending delete and an approval card fires to Iosif; the mailbox is removed only
    after he taps Approve. Tell the user the request is pending founder approval."""
    try:
        local, domain = _split_email(email)
        full = f"{local}@{domain}"
        pending_id = enqueue_mailbox_delete(full, requested_by="sophia")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Could not queue delete: {e}"}
    return {
        "ok": True,
        "pending": True,
        "pending_id": pending_id,
        "email": full,
        "message": (
            f"⚠️ Deletion of {full} is DESTRUCTIVE and requires founder approval. "
            "An approval card has been sent to Iosif via Telegram — the mailbox will be "
            "removed only after he taps Approve. Tell the user it's pending Iosif's confirmation."
        ),
    }
