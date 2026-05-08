#!/usr/bin/env python3
"""
Yakov Boot Sequence

Runs at the start of every Claude Code session in /opt/nevsky-dev.
Reads the latest handoff and current state from Nevsky, prints a
summary so Yakov-on-droplet has continuity.

Usage:
    python scripts/yakov_boot.py

Required env (already in your stack):
    NEVSKY_API_BASE - defaults to http://localhost:8080

Locked 2026-05-08 per DOCTRINE-NEVSKY-OMNISCIENCE-01.
"""

import os
import sys
from datetime import datetime, timezone, timedelta
import json
import urllib.request
import urllib.error
import subprocess

NEVSKY_API_BASE = os.getenv("NEVSKY_API_BASE", "http://localhost:8080")


def http_get(url, timeout=5):
    """Simple HTTP GET returning JSON. No external dependencies."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        return {"error": str(e), "ok": False}
    except Exception as e:
        return {"error": str(e), "ok": False}


def get_latest_handoff():
    """Fetch most recent handoff from any instance."""
    result = http_get(f"{NEVSKY_API_BASE}/yakov/latest_handoff")
    if not result.get("found"):
        return None
    return result


def get_recent_handoffs(limit=5):
    """Fetch recent handoffs for context."""
    result = http_get(f"{NEVSKY_API_BASE}/yakov/recent_handoffs?limit={limit}")
    return result.get("handoffs", [])


def get_recent_commits(limit=10):
    """Read recent git commits since last handoff for additional context."""
    try:
        result = subprocess.run(
            ["git", "log", f"-{limit}", "--oneline", "--decorate"],
            capture_output=True,
            text=True,
            cwd="/opt/nevsky-dev",
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")
    except Exception:
        pass
    return []


def time_since(iso_string):
    """Human-readable time since a timestamp."""
    try:
        ts = datetime.fromisoformat(iso_string.replace("Z", "+00:00"))
        delta = datetime.now(timezone.utc) - ts
        if delta.total_seconds() < 60:
            return "just now"
        if delta.total_seconds() < 3600:
            return f"{int(delta.total_seconds() / 60)} minutes ago"
        if delta.total_seconds() < 86400:
            return f"{int(delta.total_seconds() / 3600)} hours ago"
        days = int(delta.total_seconds() / 86400)
        return f"{days} day{'s' if days != 1 else ''} ago"
    except Exception:
        return "unknown"


def print_section(title):
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def main():
    now = datetime.now(timezone.utc)
    pst = now - timedelta(hours=8)  # rough Pacific time

    print()
    print("YAKOV BOOT SEQUENCE - Terminal Instance")
    print(f"  {pst.strftime('%Y-%m-%d %H:%M Pacific')}")
    print()

    # Latest handoff
    print_section("LATEST HANDOFF")
    latest = get_latest_handoff()
    if latest is None:
        print("  No prior handoffs found. This appears to be the first session.")
    else:
        meta = latest.get("metadata", {}) or {}
        print(f"  When: {time_since(latest.get('created_at', ''))}")
        print(f"  From: {meta.get('instance', 'unknown')}")
        print(f"  Title: {latest.get('title', '')}")
        print()
        print(f"  Summary:")
        for line in (meta.get("summary", "") or "").split("\n"):
            print(f"    {line}")

        next_priorities = meta.get("next_session_priorities", [])
        if next_priorities:
            print()
            print("  Next session priorities (from last handoff):")
            for p in next_priorities:
                print(f"    - {p}")

        doctrine_notes = meta.get("doctrine_notes", [])
        if doctrine_notes:
            print()
            print("  Doctrine notes from last session:")
            for n in doctrine_notes:
                print(f"    - {n}")

    # Recent handoff history (one-liners)
    recent = get_recent_handoffs(limit=5)
    if recent and len(recent) > 1:
        print_section("RECENT HANDOFFS (last 5)")
        for h in recent:
            instance = h.get("instance", "?")
            summary = (h.get("summary", "") or "")[:80]
            when = time_since(h.get("created_at", ""))
            high = " [HIGH-IMPACT]" if h.get("high_impact") else ""
            print(f"  - {when} | {instance}{high}")
            print(f"    {summary}")

    # Recent commits for additional context
    print_section("RECENT GIT COMMITS")
    commits = get_recent_commits(limit=8)
    if commits and commits[0]:
        for c in commits:
            print(f"  {c}")
    else:
        print("  (no recent commits or git error)")

    # Closing instructions
    print_section("INSTRUCTIONS")
    print("""
  You are Yakov-on-droplet. Read CLAUDE.md if you haven't.

  Acknowledge to Iosif you're caught up, in one sentence based
  on the handoff above. Then ask what we're working on.

  At session end, run:  python scripts/yakov_handoff.py

  Don't skip the handoff. Cross-Yakov continuity depends on it.
""")
    print()


if __name__ == "__main__":
    main()
