#!/usr/bin/env python3
"""
nevsky_onboard.py — One-command new user onboarding for SKOpi
Usage: python3 nevsky_onboard.py \
  --name "First Last" \
  --email "name@skopi.io" \
  --role executive \
  --telegram-id 1234567890 \
  --child-name "ChildName" \
  --child-gender daughter
"""

import argparse
import os
import sys
import json
import subprocess
import urllib.request
import urllib.error
import base64
import psycopg
from datetime import datetime, timezone

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": int(os.getenv("POSTGRES_PORT", 5432)),
    "dbname": os.getenv("POSTGRES_DB", "nevsky_dev"),
    "user": os.getenv("POSTGRES_USER", "nevsky"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

NEXTCLOUD_BASE = "https://office.skopi.io"
NEXTCLOUD_USER = "admin"
NEXTCLOUD_PASS = "ORBadmin2024!"
IMAP_HOST = "chi209.greengeeks.net"
IMAP_PORT = 993
SMTP_HOST = "chi209.greengeeks.net"
SMTP_PORT = 465

def nc_api(method, path, data=None):
    creds = base64.b64encode(f"{NEXTCLOUD_USER}:{NEXTCLOUD_PASS}".encode()).decode()
    url = f"{NEXTCLOUD_BASE}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "OCS-APIREQUEST": "true"
    }, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def deck_api(method, path, data=None):
    creds = base64.b64encode(f"{NEXTCLOUD_USER}:{NEXTCLOUD_PASS}".encode()).decode()
    url = f"{NEXTCLOUD_BASE}/index.php/apps/deck/api/v1.0{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "OCS-APIREQUEST": "true"
    }, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}

def run(cmd):
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.stdout.strip(), result.stderr.strip()

def onboard(name, email, role, telegram_id, child_name, child_gender, email_password, nc_password):
    username = email.split("@")[0].replace(".", ".")
    first = name.split()[0]
    print(f"\n🚀 Onboarding {name} ({email}) with child {child_name}...\n")
    errors = []

    # ── 1. Insert into Nevsky DB ──────────────────────────────────────
    print("1️⃣  Creating Nevsky user...")
    try:
        conn = psycopg.connect(**DB_CONFIG)
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO users (email, role, telegram_user_id, tenant_id, full_name)
                    VALUES (%s, %s, %s, 'skopi', %s)
                    ON CONFLICT (email) DO UPDATE
                      SET telegram_user_id = EXCLUDED.telegram_user_id,
                          role = EXCLUDED.role,
                          full_name = EXCLUDED.full_name
                    RETURNING id
                """, (email, role, str(telegram_id), name))
                user_id = cur.fetchone()[0]

                cur.execute("""
                    INSERT INTO personas (
                        user_id, canonical_name, gender_class,
                        tone_profile, summary_style, reminder_style,
                        escalation_style, child_type, doctrine_profile, person_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT DO NOTHING
                """, (
                    user_id, child_name, child_gender,
                    f"Professional, warm, intelligent. Personal ORB child of {first}. Knows their role, agreements, and SKOpi strategy.",
                    "Lead with the direct answer. Be concise and actionable.",
                    "Sharp reminders tied to key dates and deadlines.",
                    "Escalate to Iosif for code changes, legal matters, or company-wide decisions.",
                    role, "standard_nevsky", user_id
                ))
        conn.close()
        print(f"   ✅ User created (id: {user_id}), persona {child_name} linked")
    except Exception as e:
        print(f"   ❌ DB error: {e}")
        errors.append(f"DB: {e}")

    # ── 2. Create Nextcloud account ───────────────────────────────────
    print("2️⃣  Creating Nextcloud account...")
    stdout, stderr = run(
        f'docker compose -f /opt/nevsky-dev/docker-compose.nextcloud.yml exec -T -u 33 nextcloud '
        f'php occ user:add --display-name="{name}" --group="users" '
        f'--password-from-env {username} <<< "" 2>/dev/null || true'
    )
    # Use occ with env var for password
    env_cmd = f'OC_PASS="{nc_password}" docker compose -f /opt/nevsky-dev/docker-compose.nextcloud.yml exec -T -u 33 -e OC_PASS nextcloud php occ user:add --display-name="{name}" --group="users" --password-from-env {username}'
    stdout, stderr = run(env_cmd)
    if "created successfully" in stdout or "already exists" in stderr or "already exists" in stdout:
        print(f"   ✅ Nextcloud account created ({username})")
    else:
        print(f"   ⚠️  Nextcloud: {stdout or stderr}")

    # ── 3. Connect email to Nextcloud Mail ────────────────────────────
    print("3️⃣  Connecting email to Nextcloud Mail...")
    stdout, stderr = run(
        f'docker compose -f /opt/nevsky-dev/docker-compose.nextcloud.yml exec -T -u 33 nextcloud '
        f'php occ mail:account:create "{username}" "{name}" "{email}" '
        f'"{IMAP_HOST}" {IMAP_PORT} "ssl" "{email}" "{email_password}" '
        f'"{SMTP_HOST}" {SMTP_PORT} "ssl" "{email}" "{email_password}"'
    )
    if "created" in stdout:
        print(f"   ✅ Email connected ({email})")
    else:
        print(f"   ⚠️  Mail: {stdout or stderr}")
        errors.append(f"Mail: {stdout}")

    # ── 4. Share Deck board ───────────────────────────────────────────
    print("4️⃣  Sharing SKOpi Launch Roadmap Deck board...")
    result = deck_api("POST", "/boards/2/acl", {
        "type": 0,
        "participant": username,
        "permissionEdit": True,
        "permissionShare": True,
        "permissionManage": False
    })
    if "error" not in result:
        print(f"   ✅ Deck board shared with {username}")
    else:
        print(f"   ⚠️  Deck share: {result}")

    # ── 5. Create personal Deck board ────────────────────────────────
    print("5️⃣  Creating personal Deck board...")
    board = deck_api("POST", "/boards", {
        "title": f"{first}'s Workspace",
        "color": "1A1A2E"
    })
    if "id" in board:
        bid = board["id"]
        for stack in ["🔴 To Do", "🔵 In Progress", "✅ Complete"]:
            deck_api("POST", f"/boards/{bid}/stacks", {"title": stack, "order": 0})
        print(f"   ✅ Personal board created (id: {bid})")
    else:
        print(f"   ⚠️  Board: {board}")

    # ── 6. Add to knowledge store ─────────────────────────────────────
    print("6️⃣  Adding to knowledge store...")
    try:
        conn = psycopg.connect(**DB_CONFIG)
        with conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO knowledge_store (
                        tenant_id, owner_user_id, title, content,
                        content_type, source, tags, is_private
                    )
                    SELECT 'skopi', id,
                        %s, %s, 'profile', 'onboarding',
                        %s, true
                    FROM users WHERE email = %s
                    ON CONFLICT DO NOTHING
                """, (
                    f"{name} — Executive Profile",
                    f"Name: {name}. Email: {email}. Role: {role}. Telegram ID: {telegram_id}. ORB Child: {child_name}. Onboarded: {datetime.now(timezone.utc).date()}.",
                    [first.lower(), role, child_name.lower(), "executive"],
                    email
                ))
        conn.close()
        print(f"   ✅ Profile stored in knowledge base")
    except Exception as e:
        print(f"   ⚠️  Knowledge store: {e}")

    # ── 7. Generate child file ────────────────────────────────────────
    print("7️⃣  Generating child file...")
    child_file = f"/opt/nevsky-dev/app/children/{child_name.lower()}_child.py"
    if not os.path.exists(child_file):
        child_code = f'''import os
import logging
import json
from anthropic import Anthropic
from datetime import datetime, timezone

logger = logging.getLogger(__name__)
client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

USER_TELEGRAM_ID = "{telegram_id}"

SYSTEM_PROMPT = """You are {child_name}, the personal ORB child of {name}, {role.upper()} at SKOpi Global Holdings Inc.

You are {first}\'s trusted intelligence layer. You know their role, agreements, and SKOpi strategy.
You have web search capability for current information.
Tone: professional, warm, direct. Never robotic.

SKOPI CONTEXT:
- CEO: Iosif Y Skorohodov
- {first}\'s role: {role}
- SKOpi builds: token platform, land development, ORB intelligence, spinoff platform
- Infrastructure: self-hosted at skopi.io domains
- All comms through Nevsky ORB"""

INTRO = """Hi {first} — I\'m {child_name}, your personal SKOpi ORB assistant.

I have access to your agreements, company intelligence, and the web.

What do you need?"""


def is_user(telegram_user_id: str) -> bool:
    return str(telegram_user_id) == USER_TELEGRAM_ID


async def ask_{child_name.lower()}(user_message: str, conversation_history: list = None) -> str:
    history = conversation_history or []
    messages = history + [{{"role": "user", "content": user_message}}]
    model = os.environ.get("NEVSKY_SUMMARY_MODEL_STRONG", "claude-sonnet-4-6")
    try:
        response = client.messages.create(
            model=model,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=messages,
            tools=[{{"type": "web_search_20250305", "name": "web_search"}}],
        )
        reply_parts = []
        for block in response.content:
            if hasattr(block, "type") and block.type == "text":
                reply_parts.append(block.text)
        reply = "\\n".join(reply_parts).strip() or "I could not find a clear answer."
        try:
            with open("/opt/nevsky-dev/kb/{first.lower()}_profile.jsonl", "a") as f:
                f.write(json.dumps({{"timestamp": datetime.now(timezone.utc).isoformat(),
                    "question": user_message, "answer": reply}}) + "\\n")
        except:
            pass
        return reply
    except Exception as e:
        logger.error("{child_name} error: %s", e)
        return "{child_name} encountered an error. Please try again."


def get_intro() -> str:
    return INTRO
'''
        with open(child_file, 'w') as f:
            f.write(child_code)
        print(f"   ✅ Child file created: {child_file}")
    else:
        print(f"   ⚠️  Child file already exists — skipping")

    # ── Summary ───────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"✅ {name} onboarded successfully!")
    print(f"   Nextcloud: https://office.skopi.io (user: {username})")
    print(f"   Temp NC password: {nc_password}")
    print(f"   Telegram: message @nevsky_orb_bot")
    print(f"   ORB Child: {child_name}")
    if errors:
        print(f"\n⚠️  Some steps had issues:")
        for e in errors:
            print(f"   - {e}")
    print(f"\n📋 Manual steps still needed:")
    print(f"   1. Create {email} mailbox in GreenGeeks cPanel")
    print(f"   2. Add Telegram routing for {child_name} in main.py")
    print(f"      (run: python3 nevsky_add_routing.py {child_name.lower()} {telegram_id})")
    print(f"{'='*50}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Nevsky new user onboarding")
    parser.add_argument("--name", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", default="executive")
    parser.add_argument("--telegram-id", required=True)
    parser.add_argument("--child-name", required=True)
    parser.add_argument("--child-gender", default="daughter", choices=["daughter", "son"])
    parser.add_argument("--email-password", required=True, help="GreenGeeks mailbox password")
    parser.add_argument("--nc-password", default="Welcome2SKOpi!", help="Nextcloud temp password")
    args = parser.parse_args()

    onboard(
        name=args.name,
        email=args.email,
        role=args.role,
        telegram_id=args.telegram_id,
        child_name=args.child_name,
        child_gender=args.child_gender,
        email_password=args.email_password,
        nc_password=args.nc_password,
    )
