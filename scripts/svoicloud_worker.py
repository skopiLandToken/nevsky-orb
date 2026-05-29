#!/usr/bin/env python3
"""
svoicloud_worker.py — host-side root executor for per-MP SVOIcloud stacks.
DOCTRINE-MP-ISOLATION-01.  systemd unit: svoicloud-worker.service.

WHY A HOST DAEMON (not the api container):
  Provisioning is host-root work — `docker compose up`, nginx vhost + reload, OCC
  inside the MP container, and Wasabi bucket/key creation. The nevsky-api container
  is python-slim with none of that, and giving it docker.sock = root-on-host behind
  the internet edge (six-locks: never). So the Sophia tool only ENQUEUES
  (app/integrations/svoicloud_provisioner.py) and this daemon executes. Same split as
  yindo_worker.py.

WORK SIGNALS (DB-polled, durable across restarts):
  svoicloud_stacks.status = 'queued'                -> provision_one()
  svoicloud_pending_deprovisions.status = 'approved' -> deprovision_one()

Every meaningful step writes a svoicloud_provisioning_log row (success AND failure).
Admin password + one-time QR link are delivered to Iosif via the Sophia Telegram
channel — NEVER logged, committed, or emailed.
"""

import os
import re
import json
import time
import string
import secrets
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta

import psycopg
import httpx

try:
    import boto3
    from botocore.client import Config as BotoConfig
    from botocore.exceptions import ClientError
except Exception as _e:  # noqa: BLE001
    boto3 = None

try:
    from jinja2 import Template
except Exception:  # noqa: BLE001
    Template = None

logging.basicConfig(level=logging.INFO, format="[svoicloud-worker %(asctime)s] %(message)s")
log = logging.getLogger("svoicloud-worker").info


# ── env (systemd provides via EnvironmentFile; load .env when run manually) ──────
def _load_env_if_present():
    p = Path("/opt/nevsky-dev/.env")
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_env_if_present()

BASE_DOMAIN   = os.getenv("SVOICLOUD_BASE_DOMAIN", "cloud.skopi.io")
MP_DIR        = Path(os.getenv("SVOICLOUD_MP_DIR", "/opt/svoicloud/mps"))
TEMPLATES_DIR = Path(os.getenv("SVOICLOUD_TEMPLATES_DIR", "/opt/svoicloud/templates"))
CERT_FULLCHAIN = os.getenv("SVOICLOUD_WILDCARD_FULLCHAIN", "/opt/svoicloud/certs/cloud.skopi.io.fullchain.pem")
CERT_KEY       = os.getenv("SVOICLOUD_WILDCARD_KEY", "/opt/svoicloud/certs/cloud.skopi.io.key")
NGINX_SITES_AVAILABLE = Path("/etc/nginx/sites-available")
NGINX_SITES_ENABLED   = Path("/etc/nginx/sites-enabled")

PORT_BASE = int(os.getenv("SVOICLOUD_PORT_BASE", "33000"))
PORT_MAX  = int(os.getenv("SVOICLOUD_PORT_MAX", "33999"))

WASABI_ENDPOINT = os.getenv("WASABI_ENDPOINT", "https://s3.us-east-1.wasabisys.com")
WASABI_REGION   = os.getenv("WASABI_REGION", "us-east-1")
WASABI_BACKUP_ENDPOINT = os.getenv("WASABI_BACKUP_ENDPOINT", "https://s3.us-west-1.wasabisys.com")
WASABI_BACKUP_REGION   = os.getenv("WASABI_BACKUP_REGION", "us-west-1")
WASABI_KEY    = os.getenv("WASABI_ACCESS_KEY_ID", "")
WASABI_SECRET = os.getenv("WASABI_SECRET_ACCESS_KEY", "")

SOPHIA_BOT_TOKEN = os.getenv("SOPHIA_BOT_TOKEN", "")
IOSIF_TELEGRAM_ID = os.getenv("IOSIF_TELEGRAM_ID", "7583693994")

POLL_SECONDS = int(os.getenv("SVOICLOUD_POLL_SECONDS", "4"))

PG = dict(
    host=os.getenv("SVOICLOUD_PG_HOST", "127.0.0.1"),
    port=int(os.getenv("POSTGRES_PORT", "5432")),
    dbname=os.getenv("POSTGRES_DB", "nevsky_dev"),
    user=os.getenv("POSTGRES_USER", "nevsky"),
    password=os.getenv("POSTGRES_PASSWORD", "change_me_now"),
)


# ── infra helpers ────────────────────────────────────────────────────────────
def db():
    return psycopg.connect(**PG)


def audit(cur_or_none, operation, slug, success, detail=None, error=None, invoked_by="worker"):
    """Best-effort log row. Uses its own connection so it can't poison a txn."""
    try:
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """INSERT INTO svoicloud_provisioning_log
                       (operation, mp_slug, invoked_by, success, detail, error_message)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)""",
                (operation, slug, invoked_by, success,
                 json.dumps(detail, default=str) if detail is not None else None, error),
            )
            conn.commit()
    except Exception as e:  # noqa: BLE001
        log(f"AUDIT WRITE FAILED op={operation} slug={slug}: {e}")


def gen_password(length=24):
    syms = "!@#$%^&*-_=+"
    pool = string.ascii_letters + string.digits + syms
    while True:
        pw = "".join(secrets.choice(pool) for _ in range(length))
        if (any(c.islower() for c in pw) and any(c.isupper() for c in pw)
                and any(c.isdigit() for c in pw) and any(c in syms for c in pw)):
            return pw


def gen_token(n=32):
    return secrets.token_urlsafe(n)


def run(cmd, timeout=300, check=True, env=None):
    """Run a host command; return (rc, stdout, stderr). Logs the command (never secrets —
    callers must pass secrets via env, not argv)."""
    log(f"$ {' '.join(cmd)}")
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                       env={**os.environ, **(env or {})})
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd failed ({p.returncode}): {' '.join(cmd)}\n{p.stderr[:800]}")
    return p.returncode, p.stdout, p.stderr


def tg_send(text, markup=None):
    if not SOPHIA_BOT_TOKEN:
        log("TELEGRAM not configured (SOPHIA_BOT_TOKEN missing); skipping send")
        return
    payload = {"chat_id": int(IOSIF_TELEGRAM_ID), "text": text, "parse_mode": "Markdown"}
    if markup:
        payload["reply_markup"] = json.dumps(markup)
    try:
        r = httpx.post(f"https://api.telegram.org/bot{SOPHIA_BOT_TOKEN}/sendMessage",
                       json=payload, timeout=20)
        if r.status_code != 200:
            log(f"telegram send non-200: {r.status_code} {r.text[:200]}")
    except Exception as e:  # noqa: BLE001
        log(f"telegram send error: {e}")


def render(template_name, ctx):
    src = (TEMPLATES_DIR / template_name).read_text()
    if Template:
        return Template(src).render(**ctx)
    out = src
    for k, v in ctx.items():
        out = re.sub(r"{{\s*" + re.escape(k) + r"\s*}}", str(v), out)
    return out


# ── Wasabi ─────────────────────────────────────────────────────────────────
def s3(endpoint=WASABI_ENDPOINT, region=WASABI_REGION):
    return boto3.client("s3", endpoint_url=endpoint, region_name=region,
                        aws_access_key_id=WASABI_KEY, aws_secret_access_key=WASABI_SECRET,
                        config=BotoConfig(s3={"addressing_style": "path"}))


def wasabi_create_bucket(slug):
    bucket = f"svoicloud-mp-{slug}"
    cli = s3()
    try:
        cli.head_bucket(Bucket=bucket)
        log(f"bucket {bucket} already exists")
    except ClientError:
        cli.create_bucket(Bucket=bucket)
        log(f"created bucket {bucket}")
    return bucket


def wasabi_scoped_key(bucket):
    """Best-effort per-bucket scoped key via Wasabi IAM (AWS-compatible). If the service
    account lacks IAM rights, fall back to the service key and flag it — isolation still
    holds at the bucket/container/network level; the scoped key is defense-in-depth.
    Returns (key, secret, scoped: bool)."""
    try:
        iam = boto3.client("iam", endpoint_url="https://iam.wasabisys.com", region_name=WASABI_REGION,
                           aws_access_key_id=WASABI_KEY, aws_secret_access_key=WASABI_SECRET)
        uname = f"svc-{bucket}"[:64]
        try:
            iam.create_user(UserName=uname)
        except ClientError as e:
            if e.response["Error"]["Code"] != "EntityAlreadyExists":
                raise
        policy = {
            "Version": "2012-10-17",
            "Statement": [{
                "Effect": "Allow",
                "Action": "s3:*",
                "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"],
            }],
        }
        iam.put_user_policy(UserName=uname, PolicyName=f"{bucket}-scope",
                            PolicyDocument=json.dumps(policy))
        ak = iam.create_access_key(UserName=uname)["AccessKey"]
        return ak["AccessKeyId"], ak["SecretAccessKey"], True
    except Exception as e:  # noqa: BLE001
        log(f"WASABI IAM scoped-key unavailable ({e}); falling back to service key. "
            "TASK-SVOICLOUD-SCOPED-KEYS-01: enable IAM on the service account for per-bucket keys.")
        return WASABI_KEY, WASABI_SECRET, False


def wasabi_delete_bucket(slug, archive_to=None):
    bucket = f"svoicloud-mp-{slug}"
    cli = s3()
    try:
        if archive_to:
            dst = s3(WASABI_BACKUP_ENDPOINT, WASABI_BACKUP_REGION)
            try:
                dst.head_bucket(Bucket=archive_to)
            except ClientError:
                dst.create_bucket(Bucket=archive_to)
            paginator = cli.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=bucket):
                for obj in page.get("Contents", []):
                    body = cli.get_object(Bucket=bucket, Key=obj["Key"])["Body"].read()
                    dst.put_object(Bucket=archive_to, Key=f"{slug}/{obj['Key']}", Body=body)
            log(f"archived {bucket} -> {archive_to} (region {WASABI_BACKUP_REGION})")
        # empty then delete
        paginator = cli.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket):
            keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
            if keys:
                cli.delete_objects(Bucket=bucket, Delete={"Objects": keys})
        cli.delete_bucket(Bucket=bucket)
        log(f"deleted bucket {bucket}")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("NoSuchBucket", "404"):
            log(f"bucket {bucket} already gone")
        else:
            raise


# ── port allocation ───────────────────────────────────────────────────────
def allocate_port():
    with db() as conn, conn.cursor() as cur:
        cur.execute("SELECT http_port FROM svoicloud_stacks WHERE http_port IS NOT NULL")
        used = {r[0] for r in cur.fetchall()}
    # also avoid anything currently listening
    _, ss_out, _ = run(["ss", "-tlnH"], check=False)
    for line in ss_out.splitlines():
        m = re.search(r":(\d+)\s", line)
        if m:
            used.add(int(m.group(1)))
    for port in range(PORT_BASE, PORT_MAX + 1):
        if port not in used:
            return port
    raise RuntimeError("no free port in SVOIcloud range")


# ── Nextcloud readiness + OCC ───────────────────────────────────────────────
def wait_nextcloud_ready(slug, port, timeout=180):
    deadline = time.time() + timeout
    url = f"http://127.0.0.1:{port}/status.php"
    while time.time() < deadline:
        try:
            r = httpx.get(url, timeout=5)
            if r.status_code == 200 and r.json().get("installed") is True:
                log(f"{slug}: nextcloud installed & ready (port {port})")
                return True
        except Exception:
            pass
        time.sleep(3)
    raise RuntimeError(f"{slug}: nextcloud not ready within {timeout}s")


def occ(slug, args, check=True, env=None):
    return run(["docker", "exec", "-u", "www-data", f"mp-{slug}-app", "php", "occ", *args],
               check=check, env=env)


def install_default_apps(slug):
    for app in ("calendar", "contacts", "notes", "deck", "spreed", "mail"):
        rc, _, err = occ(slug, ["app:install", app], check=False)
        if rc != 0:
            log(f"{slug}: app:install {app} skipped/failed (non-fatal): {err.strip()[:160]}")


def make_app_password(slug, user, login_pw):
    """occ user:add-app-password generates a revocable app token for QR login."""
    rc, out, err = occ(slug, ["user:add-app-password", user, "--password-from-env"],
                       check=False, env={"OC_PASS": login_pw})
    m = re.search(r"(?:token|password).*?:\s*([A-Za-z0-9]{10,})", out, re.I)
    if rc == 0 and m:
        return m.group(1)
    m2 = re.search(r"\b([A-Za-z0-9]{20,})\b", out)
    if m2:
        return m2.group(1)
    raise RuntimeError(f"{slug}: could not obtain app password (rc={rc}): {out[:200]} {err[:200]}")


# ── nginx vhost ────────────────────────────────────────────────────────────
def write_nginx_vhost(slug, port):
    conf = render("mp_nginx_vhost.conf.j2", dict(
        slug=slug, base_domain=BASE_DOMAIN, http_port=port,
        wildcard_fullchain=CERT_FULLCHAIN, wildcard_privkey=CERT_KEY))
    fqdn = f"{slug}.{BASE_DOMAIN}"
    avail = NGINX_SITES_AVAILABLE / fqdn
    avail.write_text(conf)
    link = NGINX_SITES_ENABLED / fqdn
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to(avail)
    rc, _, err = run(["nginx", "-t"], check=False)
    if rc != 0:
        link.unlink(missing_ok=True)  # protect the live domains
        raise RuntimeError(f"nginx -t failed, rolled back vhost for {fqdn}: {err[:300]}")
    run(["systemctl", "reload", "nginx"])
    log(f"{slug}: nginx vhost live for {fqdn}")


def remove_nginx_vhost(slug):
    fqdn = f"{slug}.{BASE_DOMAIN}"
    (NGINX_SITES_ENABLED / fqdn).unlink(missing_ok=True)
    rc, _, err = run(["nginx", "-t"], check=False)
    if rc == 0:
        run(["systemctl", "reload", "nginx"])
    (NGINX_SITES_AVAILABLE / fqdn).unlink(missing_ok=True)
    log(f"{slug}: nginx vhost removed for {fqdn}")


# ── onboard token (one-time QR) ──────────────────────────────────────────────
def create_onboard_token(slug, user, app_pw, url):
    token = gen_token()
    expires = datetime.now(timezone.utc) + timedelta(hours=24)
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO svoicloud_onboard_tokens
                   (token, mp_slug, nc_user, nc_app_password, server_url, expires_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (token, slug, user, app_pw, url, expires),
        )
        conn.commit()
    return token


# ── provision ────────────────────────────────────────────────────────────────
def set_status(slug, status, **fields):
    sets = ["status=%s", "updated_at=now()"]
    vals = [status]
    for k, v in fields.items():
        sets.append(f"{k}=%s")
        vals.append(v)
    vals.append(slug)
    with db() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE svoicloud_stacks SET {', '.join(sets)} WHERE slug=%s", vals)
        conn.commit()


def provision_one(slug, owner_email, owner_telegram_id, display_name):
    log(f"=== PROVISION {slug} ===")
    set_status(slug, "provisioning")
    fqdn = f"{slug}.{BASE_DOMAIN}"
    url = f"https://{fqdn}"
    try:
        port = allocate_port()
        bucket = wasabi_create_bucket(slug)
        audit(None, "wasabi", slug, True, {"bucket": bucket, "port": port})
        bkey, bsecret, scoped = wasabi_scoped_key(bucket)

        admin_user = os.getenv("SVOICLOUD_ADMIN_USER", "mpadmin")
        admin_pw = gen_password()
        db_pw, redis_pw = gen_password(), gen_password()

        stack_dir = MP_DIR / slug
        stack_dir.mkdir(parents=True, exist_ok=True)
        compose = render("mp_stack.yml.j2", dict(
            slug=slug, base_domain=BASE_DOMAIN, http_port=port,
            db_password=db_pw, redis_password=redis_pw,
            admin_user=admin_user, admin_password=admin_pw,
            wasabi_host=WASABI_ENDPOINT.replace("https://", "").replace("http://", ""),
            wasabi_region=WASABI_REGION, wasabi_bucket_key=bkey, wasabi_bucket_secret=bsecret))
        (stack_dir / "docker-compose.yml").write_text(compose)

        run(["docker", "compose", "-p", f"mp-{slug}", "-f", str(stack_dir / "docker-compose.yml"),
             "up", "-d"], timeout=300)
        audit(None, "docker", slug, True, {"action": "up", "project": f"mp-{slug}"})

        wait_nextcloud_ready(slug, port)
        install_default_apps(slug)
        app_pw = make_app_password(slug, admin_user, admin_pw)
        token = create_onboard_token(slug, admin_user, app_pw, url)

        write_nginx_vhost(slug, port)

        set_status(slug, "active", http_port=port, wasabi_bucket=bucket,
                   admin_user=admin_user, last_error=None)
        audit(None, "provision", slug, True,
              {"url": url, "bucket": bucket, "scoped_key": scoped, "port": port})

        # The onboarding page is served by Nevsky (nevsky.skopi.io), not the MP stack.
        onboard_base = os.getenv("SVOICLOUD_ONBOARD_BASE", "https://nevsky.skopi.io")
        onboard_url = f"{onboard_base}/svoicloud/onboard/{token}"
        scoped_note = "" if scoped else "\n⚠️ Bucket uses the shared service key (per-bucket scoped key pending — TASK-SVOICLOUD-SCOPED-KEYS-01)."
        tg_send(
            f"✅ *SVOIcloud provisioned* — `{fqdn}`\n\n"
            f"🔗 {url}\n"
            f"👤 Admin: `{admin_user}`\n"
            f"🔑 Admin password: `{admin_pw}`\n"
            f"🪣 Wasabi bucket: `{bucket}`\n\n"
            f"📲 One-time QR onboarding (24h, single-use):\n{onboard_url}"
            f"{scoped_note}\n\n"
            "_Relay credentials only to the partner. Do not echo into chat history._"
        )
        log(f"=== PROVISION {slug} COMPLETE ===")
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:500]
        log(f"PROVISION {slug} FAILED: {msg}")
        set_status(slug, "failed", last_error=msg)
        audit(None, "provision", slug, False, None, msg)
        tg_send(f"⚠️ *SVOIcloud provision FAILED* — `{fqdn}`\n\n`{msg}`\n\nStack left in 'failed'. "
                "Inspect logs before retrying.")


# ── deprovision ───────────────────────────────────────────────────────────
def deprovision_one(pending_id, slug, archive_to):
    log(f"=== DEPROVISION {slug} (pending {pending_id}) ===")
    fqdn = f"{slug}.{BASE_DOMAIN}"
    try:
        set_status(slug, "deprovisioning")
        stack_dir = MP_DIR / slug
        compose_file = stack_dir / "docker-compose.yml"
        if compose_file.exists():
            run(["docker", "compose", "-p", f"mp-{slug}", "-f", str(compose_file),
                 "down", "-v"], check=False, timeout=180)
        remove_nginx_vhost(slug)
        wasabi_delete_bucket(slug, archive_to=archive_to)
        if stack_dir.exists():
            run(["rm", "-rf", str(stack_dir)], check=False)
        set_status(slug, "deprovisioned", http_port=None, last_error=None)
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE svoicloud_pending_deprovisions
                   SET status='executed', decided_at=now() WHERE id=%s""", (pending_id,))
            conn.commit()
        audit(None, "deprovision", slug, True, {"archive_to": archive_to})
        tg_send(f"🗑️ *SVOIcloud torn down* — `{fqdn}` removed (container, db, bucket"
                f"{', archived' if archive_to else ''}). Audit-logged.")
        log(f"=== DEPROVISION {slug} COMPLETE ===")
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:500]
        log(f"DEPROVISION {slug} FAILED: {msg}")
        audit(None, "deprovision", slug, False, None, msg)
        with db() as conn, conn.cursor() as cur:
            cur.execute(
                """UPDATE svoicloud_pending_deprovisions
                   SET status='failed', error_message=%s, decided_at=now() WHERE id=%s""",
                (msg, pending_id))
            conn.commit()
        set_status(slug, "failed", last_error=msg)
        tg_send(f"⚠️ *SVOIcloud teardown FAILED* — `{fqdn}`\n\n`{msg}`")


# ── main loop ─────────────────────────────────────────────────────────────
def tick():
    with db() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT slug, owner_email, owner_telegram_id, display_name
               FROM svoicloud_stacks WHERE status='queued' ORDER BY created_at ASC LIMIT 1""")
        q = cur.fetchone()
        cur.execute(
            """SELECT id, mp_slug, archive_to
               FROM svoicloud_pending_deprovisions WHERE status='approved'
               ORDER BY requested_at ASC LIMIT 1""")
        d = cur.fetchone()
    if q:
        provision_one(q[0], q[1], q[2], q[3])
    if d:
        deprovision_one(d[0], d[1], d[2])


def main():
    if boto3 is None:
        raise SystemExit("boto3 not installed on host — required for Wasabi ops")
    log(f"svoicloud-worker up. base={BASE_DOMAIN} mp_dir={MP_DIR} poll={POLL_SECONDS}s")
    while True:
        try:
            tick()
        except Exception as e:  # noqa: BLE001
            log(f"tick error: {e}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
