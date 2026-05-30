#!/bin/bash
# svoicloud_onboard.sh — generate a SVOIcloud scan-to-login QR and deliver the link
# to the person THROUGH their child ORB (Telegram), fully inside org infra.
#
#   scripts/svoicloud_onboard.sh <nextcloud-username> [child-canonical-name]
#
# What it does:
#   1. Mints a revocable per-device app password (token never printed).
#   2. Builds the nc://login payload for the REAL Nextcloud host (office.skopi.io —
#      svoicloud.skopi.io is the tenant-router landing page, not the instance).
#   3. Writes a self-contained QR page under /share with an unguessable slug and
#      prints the https link (open on a computer, scan with the Nextcloud app).
#   4. If a child is named, sends that link to the child's bound Telegram chat via
#      the child's own bot (Sophia->Iosif, Ophelia->Dan, Lilith->Jacob). No email,
#      nothing leaves our infra except the Telegram hop the children already use.
#
# The QR link is a live credential while it exists — it's one-time-ish; delete the
# /share page after setup and revoke stale tokens:
#   docker exec -u www-data nevsky-nextcloud php occ user:auth-tokens:list <user>
set -euo pipefail

USER="${1:?usage: svoicloud_onboard.sh <nextcloud-username> [child-canonical-name]}"
CHILD="${2:-}"
SERVER="${SVOICLOUD_LOGIN_URL:-https://office.skopi.io}"
NC="${NC_CONTAINER:-nevsky-nextcloud}"
API="${API_CONTAINER:-nevsky-api}"
SHARE_HOST="${SHARE_DIR:-/opt/nevsky-dev/app/public/share}"
PUBLIC_BASE="${PUBLIC_SHARE_BASE:-https://nevsky.skopi.io/share}"

command -v qrencode >/dev/null 2>&1 || { echo "qrencode not installed: apt-get install -y qrencode" >&2; exit 1; }

# 1. mint app password (parse line after the label; never echo the token)
OUT=$(docker exec -u www-data "$NC" php occ user:auth-tokens:add "$USER" --no-interaction 2>&1)
TOKEN=$(echo "$OUT" | awk '/app password:/{getline; while($0==""){getline}; print; exit}')
[ -n "${TOKEN:-}" ] || { echo "failed to mint token for '$USER':" >&2; echo "$OUT" >&2; exit 1; }

# 2 + 3. build payload, render QR page
LOGIN_URL="nc://login/user:${USER}&password:${TOKEN}&server:${SERVER}"
SLUG="login-$(openssl rand -hex 8)"
qrencode -o /tmp/_qr_$$.png -s 10 -m 4 "$LOGIN_URL"
B64=$(base64 -w0 /tmp/_qr_$$.png); rm -f /tmp/_qr_$$.png
PAGE="${SHARE_HOST}/${SLUG}.html"
cat > "$PAGE" <<HTML
<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>SVOIcloud Login</title>
<style>body{font-family:-apple-system,Arial,sans-serif;text-align:center;background:#0b0c10;color:#eee;padding:30px}
h1{font-size:22px}img{width:320px;max-width:90vw;background:#fff;padding:16px;border-radius:12px}
p{color:#aaa;max-width:480px;margin:14px auto;line-height:1.5}</style></head>
<body><h1>Log in to SVOIcloud</h1>
<img src="data:image/png;base64,${B64}" alt="login QR">
<p>Open the <b>Nextcloud</b> app &rarr; tap <b>Log in</b> &rarr; tap the <b>QR icon</b> &rarr; point it at this code.</p>
<p>One-time setup. This page is removed after you log in.</p></body></html>
HTML
SHARE_URL="${PUBLIC_BASE}/${SLUG}.html"
echo "QR login page: ${SHARE_URL}"

# 4. deliver through the child ORB, if named
if [ -n "$CHILD" ]; then
  MSG="🔐 Your SVOIcloud setup link.

Open this on your computer and scan it with the Nextcloud app (Log in → QR icon):
${SHARE_URL}

Internal use only — this is your one-time setup."
  docker exec "$API" python /app/svoicloud_notify.py "$CHILD" "$MSG"
else
  echo "(no child named — link not delivered; pass a child canonical name as arg 2 to send it)"
fi
