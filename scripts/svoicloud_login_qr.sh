#!/bin/bash
# svoicloud_login_qr.sh — generate a scan-to-login QR for a SVOIcloud user.
#
# Onboarding in one step: run this with a username, the person scans the QR with
# the Nextcloud mobile app (Log in -> QR icon), and they're logged in. No typing
# server URLs or passwords on a phone keyboard.
#
# How it works: mints a per-device app password via occ (revocable, limited-cap),
# encodes it into the nc://login payload the Nextcloud app reads, and renders the
# QR in the terminal. The token is NEVER printed — only the QR. Treat the QR like
# a password while it's on screen; anyone who scans it logs in as that user.
#
# Revoke a device later:  docker exec -u www-data nevsky-nextcloud php occ \
#                           user:auth-tokens:list <user>   (then ...:delete <user> <id>)
set -euo pipefail

USER="${1:?usage: scripts/svoicloud_login_qr.sh <nextcloud-username>}"
SERVER="${SVOICLOUD_URL:-https://office.skopi.io}"
CONTAINER="${NC_CONTAINER:-nevsky-nextcloud}"

command -v qrencode >/dev/null 2>&1 || { echo "qrencode not installed: apt-get install -y qrencode" >&2; exit 1; }

OUT=$(docker exec -u www-data "$CONTAINER" php occ user:auth-tokens:add "$USER" --no-interaction 2>&1)
# The generated app password is the first non-empty line after the "app password:" label.
TOKEN=$(echo "$OUT" | awk '/app password:/{getline; while($0==""){getline}; print; exit}')
if [ -z "${TOKEN:-}" ]; then
  echo "Failed to mint app password for '$USER'. occ output:" >&2
  echo "$OUT" >&2
  exit 1
fi

echo "App password minted for '$USER' (len=${#TOKEN}). Scan with the Nextcloud app (Log in -> QR icon):"
echo
qrencode -t UTF8 -m 2 "nc://login/user:${USER}&password:${TOKEN}&server:${SERVER}"
echo
echo "Token not printed. List/revoke devices: docker exec -u www-data $CONTAINER php occ user:auth-tokens:list $USER"
