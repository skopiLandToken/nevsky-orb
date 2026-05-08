#!/usr/bin/env python3
"""
Yakov Session Handoff

Run at the end of every Claude Code session in /opt/nevsky-dev.
Builds a structured handoff and POSTs it to the Nevsky handoff endpoint.

Usage:
    python scripts/yakov_handoff.py

If the endpoint is unreachable, falls back to writing the handoff to
handoff_log/YYYY-MM-DD_HHMMSS.md so nothing is lost.

Locked 2026-05-08 per DOCTRINE-NEVSKY-OMNISCIENCE-01.
"""

import os
import sys
import json
import uuid
import argparse
import subprocess
from pathlib import Path
from datetime import datetime, timezone, timedelta
import urllib.request
import urllib.error

NEVSKY_API_BASE = os.getenv("NEVSKY_API_BASE", "http://localhost:8080")
HANDOFF_LOG_DIR = Path("/opt/nevsky-dev/handoff_log")
HANDOFF_LOG_DIR.mkdir(exist_ok=True)


VALID_CHANGE_TYPES = [
    "fix", "feature", "doctrine", "schema",
    "config", "dependency", "failed_approach"
]


def prompt(label, default=""):
    """Single-line prompt with optional default."""
    suffix = f" [{default}]" if default else ""
    response = input(f"{label}{suffix}: ").strip()
    return response if response else default


def prompt_multiline(label):
    """Multi-line prompt. End with empty line."""
    print(f"{label} (end with empty line):")
    lines = []
    while True:
        line = input()
        if not line:
            break
        lines.append(line)
    return "\n".join(lines)


def prompt_list(label):
    """Prompt for a list. One item per line. Empty line ends."""
    print(f"{label} (one per line, empty line to finish):")
    items = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        items.append(line)
    return items


def prompt_yes_no(label, default=False):
    default_str = "Y/n" if default else "y/N"
    response = input(f"{label} [{default_str}]: ").strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


def get_recent_commits():
    """Pull recent commits as suggestions for what to log."""
    try:
        result = subprocess.run(
            ["git", "log", "-15", "--oneline"],
            capture_output=True, text=True, cwd="/opt/nevsky-dev", timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip().split("\n")
    except Exception:
        pass
    return []


def gather_change():
    """Interactive prompt for one change."""
    print()
    print("-" * 50)
    print("  Add a change")
    print("-" * 50)
    print(f"  Types: {', '.join(VALID_CHANGE_TYPES)}")
    change_type = prompt("Type")
    if not change_type:
        return None
    if change_type not in VALID_CHANGE_TYPES:
        print(f"  Invalid type. Must be one of: {VALID_CHANGE_TYPES}")
        return None

    description = prompt("Description (one line)")
    if not description:
        return None

    files_modified = prompt_list("Files modified")
    commit_hash = prompt("Commit hash (empty if none)")
    tags = prompt_list("Additional tags")

    return {
        "type": change_type,
        "description": description,
        "files_modified": files_modified,
        "commit_hash": commit_hash if commit_hash else None,
        "tags": tags
    }


def fallback_write_local(payload):
    """Write handoff to local file as fallback."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = HANDOFF_LOG_DIR / f"{ts}.json"
    path.write_text(json.dumps(payload, indent=2, default=str))
    return path


def submit_payload(payload):
    """POST a payload to the handoff endpoint. Falls back to local file on failure."""
    body = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(
        f"{NEVSKY_API_BASE}/yakov/handoff",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            response = json.loads(r.read().decode())
        print("Handoff ingested.")
        print(f"  knowledge_store id: {response.get('knowledge_store_id') or response.get('id')}")
        if response.get("notification_sent"):
            print("  High-impact Telegram notification sent to Iosif.")
        elif payload.get("high_impact"):
            print("  High-impact flagged but Telegram notification failed.")
        return 0
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print(f"Endpoint POST failed: {e}")
        path = fallback_write_local(payload)
        print(f"  Fallback: handoff written to {path}")
        print("  Tell Iosif the endpoint is down so he can investigate.")
        return 2


def run_from_file(path: str) -> int:
    """Read a prebuilt handoff JSON from disk and submit it. No prompts."""
    p = Path(path)
    if not p.exists():
        print(f"  --from-file path not found: {path}")
        return 1
    try:
        payload = json.loads(p.read_text())
    except json.JSONDecodeError as e:
        print(f"  Invalid JSON in {path}: {e}")
        return 1
    if not isinstance(payload, dict) or not payload.get("summary"):
        print("  Payload missing required 'summary' field.")
        return 1
    payload.setdefault("session_id", str(uuid.uuid4()))
    payload.setdefault("instance", "yakov-droplet")
    now_iso = datetime.now(timezone.utc).isoformat()
    payload.setdefault("ended_at", now_iso)
    payload.setdefault("started_at", now_iso)
    print()
    print("HANDOFF PREVIEW (from file)")
    print(json.dumps(payload, indent=2, default=str))
    print()
    return submit_payload(payload)


def main():
    parser = argparse.ArgumentParser(description="Yakov session handoff")
    parser.add_argument(
        "--from-file",
        dest="from_file",
        help="Submit a prebuilt handoff JSON from this path (skips interactive prompts).",
    )
    args = parser.parse_args()
    if args.from_file:
        sys.exit(run_from_file(args.from_file))

    print()
    print("YAKOV SESSION HANDOFF")
    print()

    # Show recent commits as suggestions
    commits = get_recent_commits()
    if commits and commits[0]:
        print("  Recent commits in this repo (for reference):")
        for c in commits[:8]:
            print(f"    {c}")
        print()

    # Session metadata
    session_id = str(uuid.uuid4())
    ended_at = datetime.now(timezone.utc)

    # Default started_at to 2 hours ago
    default_started = (
        (ended_at - timedelta(hours=2))
        .replace(microsecond=0, tzinfo=None)
        .isoformat() + "Z"
    )

    print("-" * 60)
    print("  Session info")
    print("-" * 60)
    started_at_input = prompt(
        "Session start (ISO format)", default=default_started
    )
    try:
        started_at = datetime.fromisoformat(
            started_at_input.replace("Z", "+00:00")
        )
    except ValueError:
        started_at = ended_at - timedelta(hours=2)

    # Summary
    print()
    print("-" * 60)
    print("  Summary (2-3 sentences - what happened this session)")
    print("-" * 60)
    summary = prompt_multiline("Summary")
    if not summary:
        print("  Empty summary not allowed. Aborting.")
        sys.exit(1)

    # Changes
    changes = []
    print()
    print("-" * 60)
    print("  Changes")
    print("-" * 60)
    print("  (Empty type to stop adding changes)")
    while True:
        change = gather_change()
        if change is None:
            break
        changes.append(change)
        if not prompt_yes_no("Add another change?", default=False):
            break

    # Next session priorities
    print()
    print("-" * 60)
    next_priorities = prompt_list("Next session priorities")

    # Doctrine notes
    print()
    doctrine_notes = prompt_list(
        "Doctrine notes (anything Iosif marked locked)"
    )

    # High-impact
    print()
    high_impact = prompt_yes_no(
        "Is this a HIGH-IMPACT session? "
        "(production breakage, doctrine commit, major ship)",
        default=False
    )

    # Build payload
    payload = {
        "session_id": session_id,
        "instance": "yakov-droplet",
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "summary": summary,
        "changes": changes,
        "next_session_priorities": next_priorities,
        "doctrine_notes": doctrine_notes,
        "high_impact": high_impact
    }

    # Confirm
    print()
    print("-" * 60)
    print("  HANDOFF PREVIEW")
    print("-" * 60)
    print(json.dumps(payload, indent=2, default=str))
    print()
    if not prompt_yes_no("Submit this handoff?", default=True):
        print("  Aborted. Nothing written.")
        sys.exit(0)

    # POST to endpoint
    body = json.dumps(payload, default=str).encode()
    req = urllib.request.Request(
        f"{NEVSKY_API_BASE}/yakov/handoff",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            response = json.loads(r.read().decode())
        print()
        print("Handoff ingested.")
        print(f"  knowledge_store id: {response.get('knowledge_store_id')}")
        if response.get("notification_sent"):
            print("  High-impact Telegram notification sent to Iosif.")
        elif high_impact:
            print("  High-impact flagged but Telegram notification failed.")
        sys.exit(0)
    except (urllib.error.URLError, urllib.error.HTTPError, Exception) as e:
        print()
        print(f"Endpoint POST failed: {e}")
        path = fallback_write_local(payload)
        print(f"  Fallback: handoff written to {path}")
        print("  Tell Iosif the endpoint is down so he can investigate.")
        sys.exit(2)


if __name__ == "__main__":
    main()
