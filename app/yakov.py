import os
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

import psycopg
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger(__name__)
router = APIRouter()


def _conn():
    return psycopg.connect(
        host="postgres",
        dbname=os.getenv("POSTGRES_DB", "nevsky_dev"),
        user=os.getenv("POSTGRES_USER", "nevsky"),
        password=os.getenv("POSTGRES_PASSWORD", "change_me_now"),
    )


VALID_CLASSIFICATIONS = {
    "fix", "feature", "doctrine", "schema",
    "config", "dependency", "failed_approach",
}


class HandoffChange(BaseModel):
    description: str
    classification: str = "fix"
    files_modified: List[str] = Field(default_factory=list)
    commit_hash: Optional[str] = None
    tags: List[str] = Field(default_factory=list)


class HandoffRequest(BaseModel):
    summary: str
    changes: List[HandoffChange] = Field(default_factory=list)
    files_modified: List[str] = Field(default_factory=list)
    commit_hashes: List[str] = Field(default_factory=list)
    next_priorities: List[str] = Field(default_factory=list)
    doctrine_notes: List[str] = Field(default_factory=list)
    high_impact: bool = False
    instance: str = "terminal"
    session_id: Optional[str] = None
    started_at: Optional[str] = None
    ended_at: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data):
        if not isinstance(data, dict):
            return data
        if "next_session_priorities" in data and "next_priorities" not in data:
            data["next_priorities"] = data.pop("next_session_priorities")
        for c in data.get("changes") or []:
            if isinstance(c, dict) and "type" in c and "classification" not in c:
                c["classification"] = c.pop("type")
        if data.get("changes") and not data.get("commit_hashes"):
            hashes = [
                c["commit_hash"] for c in data["changes"]
                if isinstance(c, dict) and c.get("commit_hash")
            ]
            if hashes:
                data["commit_hashes"] = hashes
        if data.get("changes") and not data.get("files_modified"):
            files: List[str] = []
            for c in data["changes"]:
                if isinstance(c, dict):
                    for f in c.get("files_modified") or []:
                        if f not in files:
                            files.append(f)
            if files:
                data["files_modified"] = files
        return data


class GitCommitRequest(BaseModel):
    commit_hash: str
    author: Optional[str] = None
    subject: Optional[str] = None
    body: Optional[str] = None
    message: Optional[str] = None
    files_changed: List[str] = Field(default_factory=list)
    branch: Optional[str] = None
    timestamp: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _normalize(cls, data):
        if not isinstance(data, dict):
            return data
        msg = data.get("message")
        if msg and not data.get("subject"):
            parts = msg.split("\n", 1)
            data["subject"] = parts[0].strip()
            if len(parts) > 1 and parts[1].strip() and not data.get("body"):
                data["body"] = parts[1].strip()
        files = data.get("files_changed")
        if isinstance(files, str):
            data["files_changed"] = [
                line.strip() for line in files.splitlines() if line.strip()
            ]
        if not data.get("subject"):
            data["subject"] = "(no subject)"
        return data


def _build_handoff_markdown(req: HandoffRequest, ts_iso: str) -> str:
    lines = [
        f"# Yakov Handoff — {ts_iso}",
        "",
        f"**Instance:** {req.instance}",
        f"**High impact:** {'yes' if req.high_impact else 'no'}",
        "",
        "## Summary",
        req.summary.strip(),
        "",
    ]
    if req.changes:
        lines.append("## Changes")
        for c in req.changes:
            lines.append(f"- [{c.classification}] {c.description}")
        lines.append("")
    if req.files_modified:
        lines.append("## Files modified")
        for f in req.files_modified:
            lines.append(f"- {f}")
        lines.append("")
    if req.commit_hashes:
        lines.append("## Commits")
        for h in req.commit_hashes:
            lines.append(f"- {h}")
        lines.append("")
    if req.next_priorities:
        lines.append("## Next session priorities")
        for p in req.next_priorities:
            lines.append(f"- {p}")
        lines.append("")
    if req.doctrine_notes:
        lines.append("## Doctrine notes")
        for d in req.doctrine_notes:
            lines.append(f"- {d}")
        lines.append("")
    return "\n".join(lines)


@router.post("/yakov/handoff")
async def post_handoff(req: HandoffRequest):
    now = datetime.now(timezone.utc)
    ts_iso = now.isoformat()
    title = f"Yakov Handoff — {now.strftime('%Y-%m-%d %H:%M UTC')}"
    content = _build_handoff_markdown(req, ts_iso)

    classifications = sorted({c.classification for c in req.changes})
    tags = ["yakov", "handoff", f"instance:{req.instance}"] + [
        f"change:{c}" for c in classifications if c in VALID_CLASSIFICATIONS
    ]
    if req.high_impact:
        tags.append("high_impact")

    metadata = {
        "type": "handoff",
        "instance": req.instance,
        "summary": req.summary,
        "changes": [c.dict() for c in req.changes],
        "files_modified": req.files_modified,
        "commit_hashes": req.commit_hashes,
        "next_priorities": req.next_priorities,
        "doctrine_notes": req.doctrine_notes,
        "high_impact": req.high_impact,
        "session_id": req.session_id,
        "started_at": req.started_at,
        "ended_at": req.ended_at,
        "captured_at": ts_iso,
    }

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_store
                        (title, content, source, source_type, content_type, tags, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        title,
                        content,
                        "yakov_terminal",
                        "yakov_handoff",
                        "session_note",
                        tags,
                        json.dumps(metadata),
                    ),
                )
                new_id = cur.fetchone()[0]
    except Exception as e:
        logger.error(f"yakov handoff insert failed: {e}")
        raise HTTPException(status_code=500, detail=f"insert failed: {e}")

    notification_sent = False
    notification_error: Optional[str] = None
    if req.high_impact:
        try:
            from main import send_sophia_message

            with _conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT bound_telegram_id FROM child_personas "
                        "WHERE canonical_name = 'sophia' "
                        "AND decommissioned_at IS NULL "
                        "AND bound_telegram_id IS NOT NULL"
                    )
                    row = cur.fetchone()

            if row and row[0]:
                chat_id = int(row[0])
                msg_lines = [
                    f"⚡ *High-impact handoff* — Yakov ({req.instance})",
                    "",
                    req.summary.strip(),
                ]
                if req.next_priorities:
                    msg_lines += ["", "*Next:*"]
                    msg_lines += [f"• {p}" for p in req.next_priorities[:5]]
                msg_lines += ["", f"_id: {new_id}_"]
                await send_sophia_message(chat_id, "\n".join(msg_lines))
                notification_sent = True
            else:
                notification_error = "no bound_telegram_id for sophia"
        except Exception as e:
            logger.warning(f"yakov handoff Telegram notify failed: {e}")
            notification_error = str(e)

    return {
        "ok": True,
        "id": str(new_id),
        "knowledge_store_id": str(new_id),
        "title": title,
        "tags": tags,
        "high_impact": req.high_impact,
        "notification_sent": notification_sent,
        "notification_error": notification_error,
    }


@router.post("/yakov/git_commit")
def post_git_commit(req: GitCommitRequest):
    now = datetime.now(timezone.utc)
    ts_iso = req.timestamp or now.isoformat()
    short = req.commit_hash[:8] if req.commit_hash else "unknown"
    subject_clip = (req.subject or "").strip().splitlines()[0][:80]
    title = f"Git commit {short}: {subject_clip}"

    body_block = f"\n\n{req.body.strip()}" if req.body else ""
    files_block = ""
    if req.files_changed:
        files_block = "\n\n## Files\n" + "\n".join(f"- {f}" for f in req.files_changed)
    content = (
        f"**Commit:** {req.commit_hash}\n"
        f"**Author:** {req.author or 'unknown'}\n"
        f"**Branch:** {req.branch or 'unknown'}\n"
        f"**Time:** {ts_iso}\n\n"
        f"{subject_clip}{body_block}{files_block}"
    )

    tags = ["yakov", "git_commit"]
    if req.branch:
        tags.append(f"branch:{req.branch}")

    metadata = {
        "type": "git_commit",
        "commit_hash": req.commit_hash,
        "author": req.author,
        "subject": req.subject,
        "body": req.body,
        "files_changed": req.files_changed,
        "branch": req.branch,
        "timestamp": ts_iso,
    }

    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO knowledge_store
                        (title, content, source, source_type, content_type, tags, metadata)
                    VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                    RETURNING id
                    """,
                    (
                        title,
                        content,
                        "yakov_terminal",
                        "git_post_commit",
                        "session_note",
                        tags,
                        json.dumps(metadata),
                    ),
                )
                new_id = cur.fetchone()[0]
    except Exception as e:
        logger.error(f"yakov git_commit insert failed: {e}")
        raise HTTPException(status_code=500, detail=f"insert failed: {e}")

    return {"ok": True, "id": str(new_id), "commit_hash": req.commit_hash}


def _row_to_handoff(row) -> dict:
    return {
        "id": str(row[0]),
        "title": row[1],
        "content": row[2],
        "tags": list(row[3]) if row[3] else [],
        "metadata": row[4],
        "created_at": row[5].isoformat() if row[5] else None,
    }


@router.get("/yakov/latest_handoff")
def get_latest_handoff():
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, content, tags, metadata, created_at
                FROM knowledge_store
                WHERE 'yakov' = ANY(tags) AND 'handoff' = ANY(tags)
                ORDER BY created_at DESC
                LIMIT 1
                """
            )
            row = cur.fetchone()
    if not row:
        return {"ok": True, "handoff": None}
    return {"ok": True, "handoff": _row_to_handoff(row)}


@router.get("/yakov/recent_handoffs")
def get_recent_handoffs(limit: int = Query(10, ge=1, le=100)):
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, content, tags, metadata, created_at
                FROM knowledge_store
                WHERE 'yakov' = ANY(tags) AND 'handoff' = ANY(tags)
                ORDER BY created_at DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = cur.fetchall()
    return {"ok": True, "count": len(rows), "handoffs": [_row_to_handoff(r) for r in rows]}
