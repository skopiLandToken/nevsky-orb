#!/usr/bin/env python3
"""
Yindo worker — host-side bridge between the Telegram webhook (in the FastAPI
container) and the Claude Code CLI (on the host).

Architecture (see CLAUDE.md "One Yakov, multiple interfaces"):

    Telegram → /telegram/yindo/webhook (container)
        → RPUSH yindo:queue { chat_id, text, ... }

    [host, systemd] yindo_worker.py (this file)
        → BLPOP yindo:queue
        → load/create yindo_session row
        → exec  claude --print --resume <uuid> --output-format json
                       --permission-mode bypassPermissions
                       --add-dir /opt/nevsky-dev
                       --max-budget-usd 5
        → chunk response (MarkdownV2, code-block-aware)
        → send via api.telegram.org
        → log to yindo_telegram_log

Runs as root on the host so Yindo via Telegram has the same filesystem access
as Yindo via SSH — per Iosif's "same Yindo, different interface" mandate. The
api container is python-slim and sandboxed; it can't run claude.

Serial by design: single owner, single queue, one subprocess at a time. The
worker is the only thing that reads/writes session_id, so no locking needed.
"""

import json
import os
import re
import signal
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psycopg
import redis as redis_lib

# Env is canonically loaded by systemd via EnvironmentFile=/opt/nevsky-dev/.env
# before the unit drops to User=yindo. The .env file stays mode 600 root:root —
# the yindo user has no read access to it. Do not re-load it here.
PROJECT_ROOT = Path("/opt/nevsky-dev")

CLAUDE_BIN = os.getenv("YINDO_CLAUDE_BIN", "/usr/bin/claude")
YINDO_BOT_TOKEN = os.getenv("YINDO_BOT_TOKEN", "").strip()
YINDO_CWD = os.getenv("YINDO_CWD", "/opt/nevsky-dev")
YINDO_MAX_BUDGET = os.getenv("YINDO_MAX_BUDGET_USD", "5")
YINDO_SOFT_BUDGET = float(os.getenv("YINDO_SOFT_BUDGET_USD", "2"))
YINDO_TIMEOUT_SEC = int(os.getenv("YINDO_TIMEOUT_SEC", "600"))  # 10 min hard wall
YINDO_QUEUE_KEY = "yindo:queue"
YINDO_CURRENT_KEY = "yindo:current"
YINDO_REDIS_HOST = os.getenv("YINDO_WORKER_REDIS_HOST", "127.0.0.1")
YINDO_REDIS_PORT = int(os.getenv("YINDO_WORKER_REDIS_PORT", "6379"))
YINDO_PG_HOST = os.getenv("YINDO_WORKER_PG_HOST", "127.0.0.1")
YINDO_PG_PORT = int(os.getenv("YINDO_WORKER_PG_PORT", os.getenv("POSTGRES_PORT", "5432")))
YINDO_PG_DB = os.getenv("POSTGRES_DB", "nevsky_dev")
YINDO_PG_USER = os.getenv("POSTGRES_USER", "nevsky")
YINDO_PG_PASSWORD = os.getenv("POSTGRES_PASSWORD", "change_me_now")

# Telegram limits — see https://core.telegram.org/bots/api#sendmessage
TELEGRAM_MAX_CHARS = 4096
CHUNK_BUDGET = 3800           # leave room for [i/n] prefix + Markdown escapes
SINGLE_MESSAGE_CAP = 3800     # below this, one message
DOCUMENT_THRESHOLD = 20000    # above this, send as .md document instead
MAX_CHUNKS = 6                # above this, send as document instead


def log(msg: str) -> None:
    """Stdout logger — systemd captures and journald-routes."""
    print(f"[yindo-worker {datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


# ============================================================
#  Redis + Postgres connections (host-side)
# ============================================================

def get_redis():
    return redis_lib.Redis(
        host=YINDO_REDIS_HOST,
        port=YINDO_REDIS_PORT,
        decode_responses=True,
        socket_keepalive=True,
    )


def get_pg():
    return psycopg.connect(
        host=YINDO_PG_HOST,
        port=YINDO_PG_PORT,
        dbname=YINDO_PG_DB,
        user=YINDO_PG_USER,
        password=YINDO_PG_PASSWORD,
    )


# ============================================================
#  Session management — keyed by Telegram chat_id
# ============================================================

def load_session(chat_id: int) -> tuple[str, bool]:
    """Return (session_id, is_new). New rows get a fresh uuid that we then pass
    to `claude --session-id`. Existing rows mean we use `--resume`."""
    with get_pg() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT session_id::text FROM yindo_session WHERE chat_id = %s",
            (chat_id,),
        )
        row = cur.fetchone()
        if row:
            return row[0], False
        new_id = str(uuid.uuid4())
        cur.execute(
            """INSERT INTO yindo_session (chat_id, session_id, cwd)
               VALUES (%s, %s, %s)""",
            (chat_id, new_id, YINDO_CWD),
        )
        conn.commit()
        return new_id, True


def bump_session(chat_id: int, cost_usd: float) -> None:
    with get_pg() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE yindo_session
               SET turns = turns + 1,
                   total_cost_usd = total_cost_usd + %s,
                   last_used_at = now()
               WHERE chat_id = %s""",
            (cost_usd, chat_id),
        )
        conn.commit()


def log_outbound(
    chat_id: int,
    body: str,
    session_id: str | None,
    duration_ms: int | None,
    cost_usd: float | None,
    chunk_count: int | None,
    metadata: dict | None = None,
) -> None:
    with get_pg() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO yindo_telegram_log
               (chat_id, direction, body, session_id, duration_ms,
                cost_usd, chunk_count, metadata)
               VALUES (%s, 'out', %s, %s, %s, %s, %s, %s)""",
            (chat_id, body, session_id, duration_ms, cost_usd, chunk_count,
             json.dumps(metadata or {})),
        )
        conn.commit()


def log_system(chat_id: int, body: str, metadata: dict | None = None) -> None:
    with get_pg() as conn, conn.cursor() as cur:
        cur.execute(
            """INSERT INTO yindo_telegram_log
               (chat_id, direction, body, metadata)
               VALUES (%s, 'system', %s, %s)""",
            (chat_id, body, json.dumps(metadata or {})),
        )
        conn.commit()


# ============================================================
#  Claude Code subprocess driver
# ============================================================

def reset_session(chat_id: int) -> str:
    """Delete the stale session row and insert a fresh UUID. Returns the new id.
    Caller is responsible for logging this transition to yindo_telegram_log so
    the audit trail captures it."""
    new_id = str(uuid.uuid4())
    with get_pg() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM yindo_session WHERE chat_id = %s", (chat_id,))
        cur.execute(
            """INSERT INTO yindo_session (chat_id, session_id, cwd)
               VALUES (%s, %s, %s)""",
            (chat_id, new_id, YINDO_CWD),
        )
        conn.commit()
    return new_id


def is_recoverable_session_error(result: dict) -> bool:
    """Pattern-match claude's stderr for failure modes that warrant wiping the
    session pointer and starting fresh. Currently: ~/.claude/sessions/<uuid> is
    missing on disk (manual cleanup, CLI version migration, user-account switch
    on the host). One-shot recovery — if a fresh session also fails, that's a
    real error and we surface it."""
    if not result.get("error"):
        return False
    stderr = result.get("stderr", "") or ""
    return "No conversation found" in stderr


def run_claude(text: str, session_id: str, is_new: bool) -> dict:
    """Execute `claude --print` and return the parsed JSON result.

    Returns the parsed JSON object on success. On failure, returns a synthetic
    dict with an `error` key so the caller can still notify Iosif gracefully.
    """
    cmd = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "json",
        "--permission-mode", "bypassPermissions",
        "--add-dir", YINDO_CWD,
        "--max-budget-usd", YINDO_MAX_BUDGET,
    ]
    if is_new:
        cmd += ["--session-id", session_id]
    else:
        cmd += ["--resume", session_id]
    cmd += [text]  # the prompt as a trailing arg

    log(f"exec: claude --print {'--session-id' if is_new else '--resume'} "
        f"{session_id} (prompt {len(text)} chars)")

    try:
        proc = subprocess.run(
            cmd,
            cwd=YINDO_CWD,
            capture_output=True,
            text=True,
            timeout=YINDO_TIMEOUT_SEC,
            env={**os.environ, "PATH": os.environ.get("PATH", "") + ":/root/.local/bin"},
        )
    except subprocess.TimeoutExpired:
        return {"error": "timeout", "result": f"Subprocess hit the {YINDO_TIMEOUT_SEC}s wall-clock cap. Killed."}
    except FileNotFoundError:
        return {"error": "claude_not_found", "result": f"claude binary missing at {CLAUDE_BIN}. Check YINDO_CLAUDE_BIN."}
    except Exception as e:
        return {"error": "subprocess_failed", "result": f"Subprocess failed: {e}"}

    if proc.returncode != 0:
        return {
            "error": f"exit_{proc.returncode}",
            "stderr": proc.stderr,  # passed through so process_job can pattern-match for recovery
            "result": (
                f"claude exited {proc.returncode}.\n\nstderr:\n{proc.stderr[-1500:]}\n\n"
                f"stdout:\n{proc.stdout[-1500:]}"
            ),
        }

    # --output-format json returns one JSON object on stdout
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        # Sometimes the CLI prints non-json prefix lines; try to find the last { ... } object
        m = re.search(r"\{.*\}\s*$", proc.stdout, flags=re.DOTALL)
        if not m:
            return {"error": "bad_json", "result": f"Could not parse claude output as JSON:\n{proc.stdout[-2000:]}"}
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError as e:
            return {"error": "bad_json", "result": f"JSON parse failed: {e}\n{proc.stdout[-2000:]}"}

    return data


# ============================================================
#  MarkdownV2 escaping + chunking
# ============================================================

# Outside formatting entities, these chars must be escaped per Telegram docs:
#   _ * [ ] ( ) ~ ` > # + - = | { } . !
# Inside ``` or ` entities, only ` and \ need escaping.
_MD_RESERVED = set(r"_*[]()~`>#+-=|{}.!")
_MD_RESERVED_IN_CODE = set("`\\")

_TRIPLE_BACKTICK_RE = re.compile(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")


def _escape_outside(text: str) -> str:
    return "".join("\\" + c if c in _MD_RESERVED else c for c in text)


def _escape_in_code(text: str) -> str:
    return "".join("\\" + c if c in _MD_RESERVED_IN_CODE else c for c in text)


def to_markdown_v2(raw: str) -> str:
    """Convert raw text (Markdown-ish) to Telegram MarkdownV2.

    Strategy:
      1. Pull triple-backtick blocks out first (placeholder them).
      2. Pull inline code out next.
      3. Escape the remaining text.
      4. Restitch with code blocks escaped per their stricter rule.
    """
    placeholders: list[tuple[str, str]] = []

    def stash_triple(m: re.Match) -> str:
        lang = m.group(1) or ""
        body = _escape_in_code(m.group(2))
        token = f"\x00YBLOCK{len(placeholders)}\x00"
        placeholders.append((token, f"```{lang}\n{body}```"))
        return token

    def stash_inline(m: re.Match) -> str:
        body = _escape_in_code(m.group(1))
        token = f"\x00YINLINE{len(placeholders)}\x00"
        placeholders.append((token, f"`{body}`"))
        return token

    stage = _TRIPLE_BACKTICK_RE.sub(stash_triple, raw)
    stage = _INLINE_CODE_RE.sub(stash_inline, stage)
    stage = _escape_outside(stage)
    for token, real in placeholders:
        # The placeholders themselves got escaped char-by-char above; restore by replacing the escaped form.
        escaped_token = _escape_outside(token)
        stage = stage.replace(escaped_token, real)
    return stage


def chunk_for_telegram(text: str) -> list[str]:
    """Split text into Telegram-sized chunks at safe boundaries.

    Splits prefer (in order): blank line, single newline, sentence end, hard cut.
    Code blocks (` ``` `) are tracked: if a split would land inside an open block,
    we close it on chunk N and reopen with the same language on chunk N+1.

    Returns a list of MarkdownV2-escaped strings ready to send.
    """
    if len(text) <= SINGLE_MESSAGE_CAP:
        return [to_markdown_v2(text)]

    # Walk the text, tracking open code-block state, accumulating chunks.
    chunks: list[str] = []
    pos = 0
    open_lang: str | None = None  # language of currently open ``` block, None if closed

    while pos < len(text):
        remaining = text[pos:]
        if len(remaining) <= CHUNK_BUDGET:
            chunk_text = remaining
            pos = len(text)
        else:
            # Find best split <= CHUNK_BUDGET away
            window = remaining[:CHUNK_BUDGET]
            split_idx = _best_split(window)
            chunk_text = remaining[:split_idx]
            pos += split_idx

        # Account for code-block state — count ``` toggles in chunk_text
        chunk_with_state, open_lang = _balance_code_fences(chunk_text, open_lang)
        chunks.append(to_markdown_v2(chunk_with_state))

    if len(chunks) == 1:
        return chunks

    # Prefix [i/n]
    n = len(chunks)
    return [f"{_escape_outside(f'[{i+1}/{n}]')} {c}" for i, c in enumerate(chunks)]


def _best_split(window: str) -> int:
    """Pick the highest-quality split index within `window`."""
    for sep in ("\n\n", "\n", ". ", " "):
        idx = window.rfind(sep)
        if idx > len(window) // 2:
            return idx + len(sep)
    return len(window)


def _balance_code_fences(chunk: str, open_lang: str | None) -> tuple[str, str | None]:
    """Ensure a chunk doesn't leave a ``` block dangling.

    If `open_lang` is set, we're entering this chunk mid-code-block; prepend a
    ``` reopen. Then count fences in the chunk to know our exit state. If we
    exit mid-block, close it before returning and pass the new open_lang
    forward so the next chunk reopens it.
    """
    prefix = f"```{open_lang or ''}\n" if open_lang is not None else ""
    body = chunk

    # Find all fence positions in body
    fences = list(re.finditer(r"```([a-zA-Z0-9_+\-]*)", body))
    state_lang = open_lang
    for f in fences:
        if state_lang is None:
            state_lang = f.group(1) or ""  # opening
        else:
            state_lang = None  # closing

    suffix = "\n```" if state_lang is not None else ""
    new_open = state_lang  # what's open at the end
    return prefix + body + suffix, new_open


# ============================================================
#  Telegram senders
# ============================================================

def telegram_send_message(chat_id: int, text: str, parse_mode: str | None = "MarkdownV2") -> dict:
    url = f"https://api.telegram.org/bot{YINDO_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    try:
        with httpx.Client() as c:
            r = c.post(url, json=payload, timeout=15)
            data = r.json()
            if not data.get("ok") and parse_mode:
                # Retry as plain text on parse error
                log(f"telegram parse error, retrying plain: {data.get('description')}")
                payload.pop("parse_mode", None)
                r = c.post(url, json=payload, timeout=15)
                data = r.json()
            return data
    except Exception as e:
        log(f"telegram_send_message exception: {e}")
        return {"ok": False, "error": str(e)}


def telegram_send_document(chat_id: int, filename: str, content: str, caption: str = "") -> dict:
    url = f"https://api.telegram.org/bot{YINDO_BOT_TOKEN}/sendDocument"
    try:
        with httpx.Client() as c:
            files = {"document": (filename, content.encode("utf-8"), "text/markdown")}
            data = {"chat_id": str(chat_id)}
            if caption:
                data["caption"] = caption[:1024]
            r = c.post(url, data=data, files=files, timeout=30)
            return r.json()
    except Exception as e:
        log(f"telegram_send_document exception: {e}")
        return {"ok": False, "error": str(e)}


def send_response(chat_id: int, body: str) -> int:
    """Send `body` to Telegram, returns number of messages sent (or 1 if document)."""
    if not YINDO_BOT_TOKEN:
        log("YINDO_BOT_TOKEN not set; skipping send")
        return 0

    body = body.strip()
    if not body:
        body = "_(empty response from claude)_"

    # Document fallback for huge responses
    if len(body) > DOCUMENT_THRESHOLD:
        filename = f"yindo-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
        summary = f"Long response ({len(body):,} chars) — see attached."
        telegram_send_document(chat_id, filename, body, caption=summary)
        return 1

    chunks = chunk_for_telegram(body)
    if len(chunks) > MAX_CHUNKS:
        filename = f"yindo-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}.md"
        summary = f"Response would need {len(chunks)} messages — sending as file instead."
        telegram_send_document(chat_id, filename, body, caption=summary)
        return 1

    for i, c in enumerate(chunks):
        result = telegram_send_message(chat_id, c)
        if not result.get("ok"):
            log(f"chunk {i+1}/{len(chunks)} failed: {result}")
        time.sleep(0.05)  # gentle pacing; well under Telegram's 30 msg/sec cap
    return len(chunks)


# ============================================================
#  Main loop
# ============================================================

_STOP = False


def _handle_sigterm(signum, frame):
    global _STOP
    log(f"received signal {signum}, will exit after current job")
    _STOP = True


def process_job(job: dict) -> None:
    chat_id = job["chat_id"]
    text = job["text"]

    # Cancel flag check (set by webhook on /cancel)
    r = get_redis()
    if r.get(f"yindo:cancel:{chat_id}"):
        log(f"cancel flag set for chat {chat_id}; skipping job")
        r.delete(f"yindo:cancel:{chat_id}")
        log_system(chat_id, "Job skipped due to /cancel flag", {"text": text})
        return

    session_id, is_new = load_session(chat_id)
    started_at = datetime.now(timezone.utc)

    # Publish "currently working" state so /status can read it
    r.set(
        YINDO_CURRENT_KEY,
        json.dumps({
            "chat_id": chat_id,
            "session_id": session_id,
            "started_at": started_at.isoformat(),
            "preview": text[:120],
        }),
        ex=YINDO_TIMEOUT_SEC + 30,
    )

    try:
        result = run_claude(text, session_id, is_new)

        # Recoverable failure mode: --resume targeted a session that no longer
        # exists on disk. Wipe the broken pointer, log the reset to the audit
        # trail, and retry exactly once with a fresh UUID. If the second call
        # also fails, that's a real error and we surface it verbatim.
        if is_recoverable_session_error(result):
            log(f"session {session_id} not on disk; wiping + retrying with new uuid")
            log_system(
                chat_id,
                f"Session reset: claude could not find session {session_id} on disk. "
                "Wiping yindo_session row and retrying with a fresh UUID. "
                "Conversation history from prior turns is lost; new context starts now.",
                {"prior_session_id": session_id, "trigger": "resume_not_found"},
            )
            session_id = reset_session(chat_id)
            result = run_claude(text, session_id, is_new=True)

        elapsed_ms = int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000)

        body = result.get("result", "") or ""
        cost = float(result.get("total_cost_usd") or 0)
        err = result.get("error")

        # Soft budget warning
        if cost >= YINDO_SOFT_BUDGET and not err:
            body = f"{body}\n\n_⚠ This turn cost ${cost:.2f} (soft warn at ${YINDO_SOFT_BUDGET})._"

        if err:
            body = f"⚠ Yindo error: `{err}`\n\n{body}"

        chunks_sent = send_response(chat_id, body)
        bump_session(chat_id, cost)
        log_outbound(
            chat_id=chat_id,
            body=body,
            session_id=session_id,
            duration_ms=elapsed_ms,
            cost_usd=cost,
            chunk_count=chunks_sent,
            metadata={
                "error": err,
                "num_turns": result.get("num_turns"),
                "is_new_session": is_new,
            },
        )
        log(f"chat {chat_id} done: {elapsed_ms}ms, ${cost:.4f}, {chunks_sent} chunks")
    finally:
        r.delete(YINDO_CURRENT_KEY)


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_sigterm)
    signal.signal(signal.SIGINT, _handle_sigterm)

    if not YINDO_BOT_TOKEN:
        log("WARNING: YINDO_BOT_TOKEN is empty. Worker will run but cannot send replies until set.")

    r = get_redis()
    try:
        r.ping()
    except Exception as e:
        log(f"redis unreachable at {YINDO_REDIS_HOST}:{YINDO_REDIS_PORT}: {e}")
        return 1
    log(f"connected to redis {YINDO_REDIS_HOST}:{YINDO_REDIS_PORT}, queue={YINDO_QUEUE_KEY}")

    while not _STOP:
        try:
            item = r.blpop(YINDO_QUEUE_KEY, timeout=10)
        except Exception as e:
            log(f"blpop error: {e}; sleeping 5s")
            time.sleep(5)
            continue
        if not item:
            continue
        _, raw = item
        try:
            job = json.loads(raw)
        except Exception as e:
            log(f"bad job json (dropping): {e} :: {raw[:200]}")
            continue
        try:
            process_job(job)
        except Exception as e:
            log(f"process_job crashed: {e}")
            try:
                telegram_send_message(
                    job["chat_id"],
                    f"_Yindo worker crashed: {e}_",
                )
                log_system(job["chat_id"], f"worker crash: {e}", {"job": job})
            except Exception:
                pass

    log("clean exit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
