"""svoicloud_notify.py — deliver a short message to a person THROUGH their child ORB.

Runs inside the api container (has the *_BOT_TOKEN env vars, psycopg, httpx). Looks
up the child's bound Telegram chat + which env var holds its bot token, then sends
via that child's own bot identity — so the person receives it from Sophia/Ophelia/
Lilith, not some generic bot. Used by scripts/svoicloud_onboard.sh to hand a new
user their login QR link without any external channel (no email, nothing leaves org
infra except the Telegram hop the children already use).

Usage (inside container):  python /app/svoicloud_notify.py <child_canonical_name> <message...>
The message is sent verbatim; the caller composes it.
"""
import os
import sys

import psycopg
import httpx


def _dsn() -> str:
    return os.environ.get("DATABASE_URL") or (
        f"postgresql://{os.environ['POSTGRES_USER']}:{os.environ['POSTGRES_PASSWORD']}"
        f"@postgres:5432/{os.environ['POSTGRES_DB']}"
    )


def notify(child: str, message: str) -> int:
    with psycopg.connect(_dsn()) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT bound_telegram_id, bot_token_env_var, display_name "
            "FROM child_personas WHERE canonical_name=%s AND decommissioned_at IS NULL",
            (child,),
        )
        row = cur.fetchone()
    if not row:
        print(f"ERROR: no active child '{child}'", file=sys.stderr)
        return 2
    chat_id, token_var, disp = row
    if not chat_id:
        print(f"ERROR: child '{child}' has no bound_telegram_id", file=sys.stderr)
        return 3
    token = os.environ.get(token_var or "", "")
    if not token:
        print(f"ERROR: bot token env '{token_var}' not set for '{child}'", file=sys.stderr)
        return 4
    # Plain text (no parse_mode) — the message carries a URL with reserved chars
    # that MarkdownV2 would choke on, and we don't want to mangle the link.
    r = httpx.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": message},
        timeout=12,
    )
    if r.status_code >= 300:
        print(f"ERROR: telegram {r.status_code}: {r.text[:200]}", file=sys.stderr)
        return 5
    print(f"delivered to {disp} (chat {chat_id}) via {token_var}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: python svoicloud_notify.py <child_canonical_name> <message...>", file=sys.stderr)
        sys.exit(1)
    sys.exit(notify(sys.argv[1], " ".join(sys.argv[2:])))
