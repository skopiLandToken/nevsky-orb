"""
svoicloud_provisioner.py — Nevsky control-plane (api-container side) for per-MP
isolated SVOIcloud stacks.  DOCTRINE-MP-ISOLATION-01 (Iosif locked 2026-05-28).

Each Marketing Partner gets a structurally isolated SVOIcloud: own Nextcloud +
Postgres + Redis container, own docker network, own Wasabi bucket + scoped key,
own credentials. Cross-MP visibility is impossible by design.

ARCHITECTURE — why this module only ENQUEUES:
  Provisioning is host-root work: `docker compose up`, nginx vhost + reload, OCC,
  and Wasabi bucket/key creation. The nevsky-api container is python-slim with no
  docker CLI, no nginx, no /opt/svoicloud mount — and handing it docker.sock would
  mean root-on-host behind the internet-facing edge (six-locks: never). So we reuse
  the proven yindo_worker split: these tools write a 'queued' row (or a pending-
  deprovision row) and return immediately; the host daemon scripts/svoicloud_worker.py
  (systemd: svoicloud-worker.service, runs as root) drives it to 'active' and delivers
  the admin password + one-time QR link to Iosif via the Sophia/Telegram channel.

  Net divergence from the brief: provisioning is ASYNC (tool returns "queued",
  completion confirmed by Sophia push ~60-90s later) rather than a synchronous
  60-second call. The brief's tool API surface is preserved.

Two write surfaces, two postures (mirror cpanel.py):
  - provision / list / status run immediately (Sophia is sovereign = Iosif's hands).
  - deprovision is DESTRUCTIVE — the tool does NOT tear down inline. It enqueues a
    pending row; a Sophia approval card fires to Iosif; the worker tears down only
    after he taps Approve.

EVERY host-side step writes a svoicloud_provisioning_log row (success AND failure) —
the error trail is the point. The api-side enqueue logs the request too.
"""

import os
import re
import json
import logging

import psycopg

logger = logging.getLogger("svoicloud")

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

BASE_DOMAIN = os.getenv("SVOICLOUD_BASE_DOMAIN", "cloud.skopi.io")

# Slugs that would collide with platform/base hostnames or look like an attack.
RESERVED_SLUGS = {"www", "admin", "api", "root", "mail", "cloud", "office",
                  "nevsky", "analytics", "onlyoffice", "sophia", "wildcard"}

# Stack states that mean "this slug is taken" — re-provision is only allowed once a
# stack is fully torn down.
LIVE_STATES = ("queued", "provisioning", "active", "deprovisioning")


class SVOICloudError(Exception):
    """Raised on control-plane failures (bad slug, collision, DB error)."""


def _conn():
    return psycopg.connect(DB_DSN)


def validate_slug(slug: str) -> str:
    """DNS-label-safe, lowercased. Reject anything that isn't a clean single label —
    it becomes <slug>.cloud.skopi.io, a docker project name, and a bucket suffix, so
    garbage here is garbage in three blast radii."""
    s = (slug or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,28}[a-z0-9])?", s):
        raise SVOICloudError(
            f"invalid slug '{slug}': use 2-30 chars, lowercase a-z/0-9/hyphen, "
            "no leading/trailing hyphen."
        )
    if s in RESERVED_SLUGS:
        raise SVOICloudError(f"slug '{s}' is reserved.")
    return s


def _log(operation: str, mp_slug: str | None, invoked_by: str,
         success: bool, detail=None, error: str | None = None) -> None:
    """Append one row to svoicloud_provisioning_log. Never raises into the caller."""
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO svoicloud_provisioning_log
                       (operation, mp_slug, invoked_by, success, detail, error_message)
                   VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
                (operation, mp_slug, invoked_by, success,
                 json.dumps(detail, default=str) if detail is not None else None, error),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        logger.error("svoicloud audit write FAILED (op=%s slug=%s): %s", operation, mp_slug, e)


# ─────────────────────────────────────────────────────────────────────────────
# Control-plane operations (enqueue + read). Raise SVOICloudError on failure.
# ─────────────────────────────────────────────────────────────────────────────
def provision_mp_stack(mp_slug: str, owner_email: str | None = None,
                       owner_telegram_id: int | None = None,
                       display_name: str | None = None,
                       requested_by: str = "sophia") -> dict:
    """Enqueue a new per-MP stack. Idempotent: a slug already live (queued/provisioning/
    active/deprovisioning) is rejected — we never clobber a running partner. A previously
    deprovisioned slug is reset to 'queued' and re-driven by the worker."""
    slug = validate_slug(mp_slug)
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM svoicloud_stacks WHERE slug=%s", (slug,))
            row = cur.fetchone()
            if row and row[0] in LIVE_STATES:
                raise SVOICloudError(
                    f"stack '{slug}' already exists (status={row[0]}). "
                    "Refusing to clobber. Deprovision first to reuse the slug."
                )
            if row:  # deprovisioned/failed → reclaim the slug
                cur.execute(
                    """UPDATE svoicloud_stacks
                       SET status='queued', owner_email=%s, owner_telegram_id=%s,
                           display_name=%s, requested_by=%s, http_port=NULL,
                           wasabi_bucket=NULL, admin_user=NULL, last_error=NULL,
                           updated_at=now()
                       WHERE slug=%s""",
                    (owner_email, owner_telegram_id, display_name, requested_by, slug),
                )
            else:
                cur.execute(
                    """INSERT INTO svoicloud_stacks
                           (slug, owner_email, owner_telegram_id, display_name, requested_by, status)
                       VALUES (%s, %s, %s, %s, %s, 'queued')""",
                    (slug, owner_email, owner_telegram_id, display_name, requested_by),
                )
            conn.commit()
    except SVOICloudError:
        raise
    except Exception as e:  # noqa: BLE001
        _log("provision", slug, requested_by, False, None, str(e))
        raise SVOICloudError(f"could not queue provision: {e}")
    _log("provision", slug, requested_by, True, {"action": "queued", "url": f"https://{slug}.{BASE_DOMAIN}"}, None)
    return {"slug": slug, "status": "queued", "url": f"https://{slug}.{BASE_DOMAIN}"}


def deprovision_mp_stack(mp_slug: str, archive_to: str | None = None,
                         requested_by: str = "sophia") -> dict:
    """Enqueue a DESTRUCTIVE teardown for founder approval. Does NOT tear down — writes
    a pending row; the approval card + the actual teardown happen elsewhere. Dedupes
    against an existing pending row for the same slug."""
    slug = validate_slug(mp_slug)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT status FROM svoicloud_stacks WHERE slug=%s", (slug,))
        row = cur.fetchone()
        if not row:
            raise SVOICloudError(f"no stack '{slug}' to deprovision.")
        if row[0] in ("deprovisioned", "deprovisioning"):
            raise SVOICloudError(f"stack '{slug}' is already {row[0]}.")
        cur.execute(
            """SELECT id FROM svoicloud_pending_deprovisions
               WHERE mp_slug=%s AND status='pending' LIMIT 1""",
            (slug,),
        )
        existing = cur.fetchone()
        if existing:
            return {"slug": slug, "pending_id": int(existing[0]), "deduped": True}
        cur.execute(
            """INSERT INTO svoicloud_pending_deprovisions (mp_slug, requested_by, archive_to)
               VALUES (%s, %s, %s) RETURNING id""",
            (slug, requested_by, archive_to),
        )
        pid = int(cur.fetchone()[0])
        conn.commit()
    _log("deprovision", slug, requested_by, True, {"action": "pending_approval", "pending_id": pid}, None)
    return {"slug": slug, "pending_id": pid, "deduped": False}


def get_mp_stack_status(mp_slug: str) -> dict:
    """Registry row + the last few log lines for one MP."""
    slug = validate_slug(mp_slug)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT slug, owner_email, owner_telegram_id, display_name, status,
                      http_port, wasabi_bucket, admin_user, last_error, created_at, updated_at
               FROM svoicloud_stacks WHERE slug=%s""",
            (slug,),
        )
        r = cur.fetchone()
        if not r:
            raise SVOICloudError(f"no stack '{slug}'.")
        cur.execute(
            """SELECT operation, success, error_message, occurred_at
               FROM svoicloud_provisioning_log WHERE mp_slug=%s
               ORDER BY occurred_at DESC LIMIT 8""",
            (slug,),
        )
        log_rows = cur.fetchall()
    return {
        "slug": r[0], "owner_email": r[1], "owner_telegram_id": r[2], "display_name": r[3],
        "status": r[4], "http_port": r[5], "wasabi_bucket": r[6], "admin_user": r[7],
        "last_error": r[8], "url": f"https://{r[0]}.{BASE_DOMAIN}",
        "created_at": str(r[9]), "updated_at": str(r[10]),
        "recent_log": [
            {"op": lr[0], "ok": lr[1], "error": lr[2], "at": str(lr[3])} for lr in log_rows
        ],
    }


def list_mp_stacks() -> list:
    """All provisioned MP stacks with a status summary."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT slug, status, owner_email, http_port, wasabi_bucket, created_at
               FROM svoicloud_stacks ORDER BY created_at DESC"""
        )
        rows = cur.fetchall()
    return [
        {"slug": r[0], "status": r[1], "owner_email": r[2], "http_port": r[3],
         "wasabi_bucket": r[4], "url": f"https://{r[0]}.{BASE_DOMAIN}", "created_at": str(r[5])}
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Sophia tool layer (sovereign only — registered in child_engine TIER_TOOLS).
# Return structured dicts (never raise) so the model gets a clean result to narrate.
# ─────────────────────────────────────────────────────────────────────────────
def sophia_provision_mp_stack(mp_slug: str, owner_email: str | None = None,
                              owner_telegram_id: int | None = None,
                              display_name: str | None = None) -> dict:
    """Queue creation of a new isolated SVOIcloud for a Marketing Partner."""
    try:
        res = provision_mp_stack(mp_slug, owner_email, owner_telegram_id, display_name,
                                 requested_by="sophia")
    except SVOICloudError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "queued": True,
        "slug": res["slug"],
        "url": res["url"],
        "message": (
            f"Provisioning of {res['url']} is queued. It comes up in ~60-90s (isolated "
            "Nextcloud + Postgres + Redis + dedicated Wasabi bucket). When it's live I'll "
            "send you the admin password and a one-time QR onboarding link via Telegram — "
            "never in chat. Tell the user their space is being prepared."
        ),
    }


def sophia_deprovision_mp_stack(mp_slug: str, archive_to: str | None = None) -> dict:
    """Request DESTRUCTIVE teardown of an MP stack. Gated — enqueues + fires an approval
    card; teardown runs only after Iosif taps Approve."""
    try:
        res = deprovision_mp_stack(mp_slug, archive_to=archive_to, requested_by="sophia")
    except SVOICloudError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "pending": True,
        "pending_id": res["pending_id"],
        "slug": res["slug"],
        "message": (
            f"⚠️ Teardown of {res['slug']}.{BASE_DOMAIN} is DESTRUCTIVE (removes the "
            "container, database, and Wasabi bucket) and requires founder approval. An "
            "approval card has been sent to Iosif via Telegram — it happens only after he "
            "taps Approve. Tell the user it's pending Iosif's confirmation; never claim it's done."
        ),
    }


def sophia_list_mp_stacks() -> dict:
    """List all provisioned SVOIcloud MP stacks and their status."""
    try:
        stacks = list_mp_stacks()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    return {"ok": True, "count": len(stacks), "stacks": stacks}


def sophia_mp_stack_status(mp_slug: str) -> dict:
    """Health/status of one MP stack: state, URL, bucket, and recent provisioning log."""
    try:
        return {"ok": True, **get_mp_stack_status(mp_slug)}
    except SVOICloudError as e:
        return {"ok": False, "error": str(e)}
