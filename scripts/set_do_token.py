#!/usr/bin/env python3
"""
set_do_token.py — paste a DigitalOcean API token, it gets written to .env safely.

Usage:
    python3 /opt/nevsky-dev/scripts/set_do_token.py

- Paste the whole dop_v1_... string when prompted (it won't display — that's intentional).
- Validates the token actually works against the DO API before saving.
- Replaces any existing DO_API_TOKEN line; never duplicates.
- Never prints the token back.
"""
import getpass
import json
import os
import sys
import urllib.request
import urllib.error

ENV_PATH = "/opt/nevsky-dev/.env"
KEY = "DO_API_TOKEN"


def prompt_token() -> str:
    print("Paste your DigitalOcean token (starts with dop_v1_) and press Enter.")
    print("Note: it will NOT show on screen as you paste — that's on purpose.\n")
    tok = getpass.getpass("DO token: ").strip()
    if not tok:
        sys.exit("No token entered. Nothing written.")
    if not tok.startswith("dop_v1_"):
        # don't hard-fail — DO could change the prefix — but warn loudly
        print("WARNING: that doesn't start with 'dop_v1_'. Continuing anyway.")
    return tok


def verify(tok: str) -> dict:
    """Hit /v2/account to prove the token is live. Returns account info or exits."""
    req = urllib.request.Request(
        "https://api.digitalocean.com/v2/account",
        headers={"Authorization": f"Bearer {tok}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.load(r).get("account", {})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            sys.exit("\nFAILED: token rejected (401). Check you copied the whole string. Nothing written.")
        sys.exit(f"\nFAILED: DO API returned HTTP {e.code}. Nothing written.")
    except Exception as e:
        sys.exit(f"\nFAILED: could not reach DO API ({e}). Nothing written.")


def write_env(tok: str) -> None:
    lines = []
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r") as f:
            lines = f.readlines()
    # drop any existing DO_API_TOKEN line(s)
    lines = [ln for ln in lines if not ln.strip().startswith(f"{KEY}=")]
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    lines.append(f"{KEY}={tok}\n")
    # write with tight perms
    fd = os.open(ENV_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.writelines(lines)


def main():
    tok = prompt_token()
    print("\nVerifying token against the DigitalOcean API...")
    acct = verify(tok)
    write_env(tok)
    email = acct.get("email", "unknown")
    status = acct.get("status", "unknown")
    droplet_limit = acct.get("droplet_limit", "?")
    print("\nSUCCESS.")
    print(f"  Token verified for account: {email} (status: {status}, droplet limit: {droplet_limit})")
    print(f"  Written to {ENV_PATH} as {KEY} (perms 0600).")
    print("\nNow tell Yakov in the chat: \"token's in\".")


if __name__ == "__main__":
    main()
