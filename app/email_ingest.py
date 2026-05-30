"""
email_ingest.py — bulk + live IMAP → knowledge_store ingestion (TASK-EMAIL-INGEST-01).

GOAL (Iosif, 2026-05-29): every mailbox across every domain on the GreenGeeks
cPanel account is pulled into knowledge_store as content_type='email' so the
children can search mail by authority tier. Bulk historical pull + live sync.

CLASSIFICATION — the load-bearing constraint (DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01):
  Email is C-level readable BY DEFAULT, with one carve-out that must never leak:
  founder-private material (Fred Jewell recordings, Dan Wikert's $97K closing
  backstop, legal threads on those topics) stays founder-only. We encode that as
  `is_private=true` on the knowledge_store row and we NEVER attach the
  `executive_readable` tag. That combination is exactly what the existing runtime
  gate in child_engine._gate_private_kb keys on:
    - sovereign (Sophia)            -> reads everything
    - executive_readall (Lilith)    -> is_private rows withheld, approval card fires
    - executive (Ophelia/Dan)       -> is_private withheld UNLESS executive_readable tag
  So classification here is just: set is_private correctly, and keep the
  executive_readable tag OFF founder-private mail. Fail closed — when a message is
  ambiguous, it is marked private.

  Two layers, OR'd together (Iosif signed off 2026-05-30):
    1. Whole-mailbox private: iosif@*, daniel.wikert@skopi.io. Dan's box is where
       the $97K backstop + legal live; safest to private the whole box, then open
       selectively. The founder's own boxes are private by default.
    2. Per-message trigger (overrides the C-level default on ANY mailbox): a
       participant or the subject/body touches Fred/Jewell, legal@, or the
       $97K-backstop vocabulary.

ACCESS / CREDENTIALS:
  We enumerate mailboxes with the DOCTRINE-MAILBOX-PROVISIONING-01 scoped cPanel
  token. cPanel does NOT expose mailbox passwords, so bulk IMAP fetch needs a
  per-mailbox password: either Iosif hands it over, or we rotate via the scoped
  token (cpanel.rotate_mailbox_password). Root cPanel creds are never used.

  FETCH IS READ-ONLY (IMAP EXAMINE / readonly=True). We must NOT set the \\Seen
  flag on real people's mail — pulling Dan's archive must be invisible to Dan.

DEDUP: idempotent on metadata->>'dedup_key' (Message-ID, or a stable hash when a
  message has no Message-ID), enforced by a partial unique index so re-running the
  bulk pull and the live poller racing each other both no-op cleanly.
"""

import os
import ssl
import json
import email
import hashlib
import logging
import imaplib
from email.header import decode_header
from email.utils import parsedate_to_datetime, getaddresses

import psycopg

logger = logging.getLogger("email_ingest")

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://nevsky:change_me_now@postgres:5432/nevsky_dev")

# Body cap. Archive value wants more than the 8k the TG bridge keeps, but we still
# bound it so one giant forwarded thread can't blow a row up.
_BODY_MAX = 20000

# ─────────────────────────────────────────────────────────────────────────────
# Classification doctrine (DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01)
# ─────────────────────────────────────────────────────────────────────────────

# Layer 1 — whole-mailbox founder-private. Every message in these boxes is private
# regardless of content. Iosif's OWN boxes were narrowed to topic-only on 2026-05-30
# (he wants C-level to read his mundane mail); the sensitive threads in his boxes are
# still caught by the Layer-2 topic trigger below. Dan's box stays whole-box private —
# the $97K backstop must never reach Dan, and his box is the highest-risk surface.
FOUNDER_PRIVATE_MAILBOXES = {
    "daniel.wikert@skopi.io",
}

# Layer 2 — per-message trigger. PRECISION MATTERS HERE: substring matching on short
# tokens ("fred", "97k", "recording") over-privatizes routine C-level mail (a real-
# estate lead, an Amazon order, a blog newsletter all tripped the first cut), which
# directly fights Iosif's "C-level access at all times" for non-carve-out mail. So we
# match on WORD BOUNDARIES and require CO-OCCURRENCE for the generic tokens. The whole-
# mailbox net (Layer 1) already blankets the founder + Dan, so Layer 2 only needs to
# catch the carve-out *topic* leaking into OTHER boxes (jgale / system / wade).
import re

# Boundaries: \b treats "_" as a word char, so r"\bjewell\b" MISSES "Fred_Jewell.pdf"
# (the real termination-letter filename). We bound on alphanumerics only, so "_", ".",
# "-" all count as separators — matches inside filenames and snake_case.
_B = r"(?<![A-Za-z0-9]){}(?![A-Za-z0-9])"

# Strong, specific single-term triggers — distinctive enough to privatize alone.
# Scanned across participants + subject + body. Case-insensitive.
_STRONG_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in (
        _B.format(r"jewell"),                 # Fred Jewell — the surname is the signal
        _B.format(r"backstop"),               # "closing backstop" — SKOpi term of art
        r"\$?" + _B.format(r"97[\s,]?000"),   # 97000 / 97,000 / 97 000 / $97,000
        r"\$?" + _B.format(r"97\s?k"),        # 97k / 97 k / $97k
        r"ninety[\s-]?seven\s+thousand",
    )
]

# "fred" alone is too common (first names, "Alfred", etc.), so it only triggers when it
# co-occurs with a recording reference — i.e. the Fred Jewell *recordings* carve-out.
_FRED = re.compile(_B.format(r"fred"), re.IGNORECASE)
_RECORDING = re.compile(_B.format(r"recordings?"), re.IGNORECASE)

# legal@ is a real carve-out vector but only for threads ON the Fred/Dan topics — a
# vendor's legal@ newsletter is not founder-private. So legal@ privatizes only when a
# carve-out topic also appears. ("dan wikert" included as a topic anchor.)
_LEGAL_ADDR = re.compile(r"legal@", re.IGNORECASE)
_DAN = re.compile(_B.format(r"wikert"), re.IGNORECASE)

# The tag the executive tier (Ophelia/Dan) gate reads to OPT a private row in.
# We import the canonical name rather than re-spelling it so a future rename of the
# doctrine constant can't silently desync this module from the gate.
EXEC_READABLE_TAG = "executive_readable"

# Map known mailbox addresses to a users.id for provenance (owner_user_id). This is
# provenance ONLY — per DOCTRINE-CLASSIFICATION-FAIL-CLOSED-01 owner_user_id NEVER
# grants read permission (subject != permission); the gate is is_private + tag.
MAILBOX_OWNER_UUID = {
    "iosif@skopi.io": "8892125a-0d06-4544-a4bf-4278ac1b1360",
    "iosif@thesko.group": "8892125a-0d06-4544-a4bf-4278ac1b1360",
    "daniel.wikert@skopi.io": "ca9e5c0d-ba19-4b2a-82fb-b2ad055f9a00",
}


def classify_email(mailbox_owner: str, participants: str, subject: str, body: str) -> dict:
    """Return {is_private, tags, reasons}. Fail closed: any positive signal => private.

    `participants` is the concatenated From/To/Cc string (already lowercased is fine,
    we lowercase here defensively). `mailbox_owner` is the box being ingested.
    """
    owner = (mailbox_owner or "").strip().lower()
    hay = f"{participants or ''}\n{subject or ''}\n{body or ''}"

    reasons: list[str] = []
    # Layer 1 — whole-mailbox private (founder + Dan).
    if owner in FOUNDER_PRIVATE_MAILBOXES:
        reasons.append(f"whole-mailbox-private:{owner}")

    # Layer 2 — precise carve-out topic detection (word-boundary, co-occurrence).
    for pat in _STRONG_PATTERNS:
        if pat.search(hay):
            reasons.append(f"strong:{pat.pattern}")
    fred = bool(_FRED.search(hay))
    if fred and _RECORDING.search(hay):
        reasons.append("fred+recording")
    if _LEGAL_ADDR.search(hay) and (fred or _DAN.search(hay)
                                    or any(p.search(hay) for p in _STRONG_PATTERNS)):
        reasons.append("legal@+carveout-topic")

    is_private = bool(reasons)

    domain = owner.split("@", 1)[1] if "@" in owner else "unknown"
    tags = ["email", f"mailbox:{owner}", f"domain:{domain}"]
    if is_private:
        # Informational marker only. We deliberately do NOT add EXEC_READABLE_TAG —
        # founder-private mail is never auto-opened to the executive tier.
        tags.append("founder_private")
    # Hard invariant: the exec-readable opt-in must never ride along on private mail.
    assert not (is_private and EXEC_READABLE_TAG in tags)

    return {"is_private": is_private, "tags": tags, "reasons": reasons}


# ─────────────────────────────────────────────────────────────────────────────
# MIME parsing (mirrors worker.py helpers; kept local so this module stands alone)
# ─────────────────────────────────────────────────────────────────────────────
def _decode_hdr(value: str | None) -> str:
    if not value:
        return ""
    out = []
    for part, enc in decode_header(value):
        if isinstance(part, bytes):
            out.append(part.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def _get_body(msg) -> str:
    """Prefer text/plain; fall back to stripped-ish text/html. Skip attachments."""
    plain, html = "", ""
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            cd = str(part.get("Content-Disposition", ""))
            if "attachment" in cd:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
            except Exception:
                continue
            if ct == "text/plain" and not plain:
                plain = text
            elif ct == "text/html" and not html:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload is not None:
                plain = payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            pass
    body = plain or html
    return body.strip()[:_BODY_MAX]


def _dedup_key(message_id: str, mailbox_owner: str, msg_from: str, subject: str, date_str: str) -> str:
    """PER-MAILBOX dedup. The key is prefixed with the mailbox so the SAME message
    living in two boxes (e.g. orb-inbound is a catch-all mirror of iosif@) is stored
    as one row PER box — each carrying its own correct classification. A global
    Message-ID key would collapse them and let a public box's classification win over
    a private box's, leaking founder-private mail. (reconcile_shared_private then
    upgrades any public copy whose Message-ID also appears in a private box.)"""
    mid = (message_id or "").strip()
    if mid:
        base = mid
    else:
        # No Message-ID: hash the stable identifying tuple so re-pulls still dedup.
        raw = f"{msg_from}|{subject}|{date_str}".encode("utf-8", errors="replace")
        base = "sha256:" + hashlib.sha256(raw).hexdigest()
    return f"{mailbox_owner}|{base}"


# ─────────────────────────────────────────────────────────────────────────────
# Storage
# ─────────────────────────────────────────────────────────────────────────────
def _conn():
    return psycopg.connect(DB_DSN)


def ensure_dedup_index() -> None:
    """Partial unique index on the dedup key for IMAP messages. Idempotent. Lets the
    bulk pull and the live poller race without producing duplicate rows."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ks_imap_dedup "
            "ON knowledge_store ((metadata->>'dedup_key')) "
            "WHERE source_type = 'imap_message'"
        )
        conn.commit()


def ingest_message(conn, *, mailbox_owner: str, folder: str, msg_from: str, msg_to: str,
                   msg_cc: str, subject: str, date_str: str, message_id: str, body: str) -> str:
    """Insert one message. Returns 'inserted' | 'duplicate'. Caller manages the txn."""
    domain = mailbox_owner.split("@", 1)[1] if "@" in mailbox_owner else "unknown"
    participants = f"{msg_from} {msg_to} {msg_cc}"
    cls = classify_email(mailbox_owner, participants, subject, body)
    dkey = _dedup_key(message_id, mailbox_owner, msg_from, subject, date_str)

    title = (subject or "(no subject)").strip()[:300]
    content = (
        f"From: {msg_from}\n"
        f"To: {msg_to}\n"
        f"Cc: {msg_cc}\n"
        f"Date: {date_str}\n"
        f"Mailbox: {mailbox_owner} (folder: {folder})\n"
        f"Subject: {subject}\n"
        f"\n{body}"
    )
    metadata = {
        "dedup_key": dkey,
        "message_id": (message_id or "").strip() or None,
        "mailbox_owner": mailbox_owner,
        "domain": domain,
        "folder": folder,
        "from": msg_from,
        "to": msg_to,
        "cc": msg_cc,
        "date": date_str,
        "classification_reasons": cls["reasons"],
    }
    owner_uuid = MAILBOX_OWNER_UUID.get(mailbox_owner)

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowledge_store
                (title, content, content_type, source, source_type, tags,
                 is_private, owner_user_id, metadata)
            VALUES (%s, %s, 'email', %s, 'imap_message', %s, %s, %s, %s::jsonb)
            ON CONFLICT ((metadata->>'dedup_key')) WHERE (source_type = 'imap_message')
            DO NOTHING
            RETURNING id
            """,
            (
                title, content, f"imap:{mailbox_owner}", cls["tags"],
                cls["is_private"], owner_uuid, json.dumps(metadata),
            ),
        )
        row = cur.fetchone()
    return "inserted" if row else "duplicate"


# ─────────────────────────────────────────────────────────────────────────────
# IMAP fetch (READ-ONLY — never mutate flags on real mailboxes)
# ─────────────────────────────────────────────────────────────────────────────
# Folder leaves skipped on auto-enumeration — pure noise that would pollute search.
# Honored only when fetch_mailbox auto-discovers folders; an explicit folders= list
# overrides (so we can deliberately pull spam/Trash if ever asked).
SKIP_FOLDER_LEAVES = {"spam", "junk", "trash"}


def _list_folders(M) -> list[str]:
    typ, data = M.list()
    if typ != "OK" or not data:
        return ["INBOX"]
    folders = []
    for raw in data:
        if not raw:
            continue
        line = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
        # LIST line: (flags) "<delim>" <name>  — the name is the trailing token and
        # may be unquoted (`... "." INBOX`) or quoted (`... "." "INBOX.Sent"`). Split
        # on the quote char: the delimiter is an interior quoted token, the name is
        # whatever trails it. Prefer the unquoted tail; fall back to the last quoted
        # token when the name itself is quoted.
        if '"' in line:
            parts = line.split('"')
            name = parts[-1].strip() or parts[-2]
        else:
            name = line.split()[-1]
        folders.append(name)
    return folders or ["INBOX"]


def fetch_mailbox(host: str, port: int, user: str, password: str,
                  folders: list[str] | None = None, search: str = "ALL",
                  limit: int | None = None):
    """Yield parsed message dicts from a mailbox, read-only across folders.

    `search` is an IMAP search criterion ("ALL" for bulk, e.g. 'SINCE 28-May-2026'
    for live sync). `limit` caps messages per folder (None = all). EXAMINE/readonly
    guarantees the \\Seen flag is never set."""
    M = imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context())
    try:
        M.login(user, password)
        if folders is not None:
            target_folders = folders
        else:
            target_folders = [f for f in _list_folders(M)
                              if f.split(".")[-1].lower() not in SKIP_FOLDER_LEAVES]
        for folder in target_folders:
            try:
                typ, _ = M.select(f'"{folder}"', readonly=True)
                if typ != "OK":
                    continue
                typ, data = M.search(None, search)
                if typ != "OK" or not data or not data[0]:
                    continue
                ids = data[0].split()
                if limit is not None:
                    ids = ids[-limit:]
                for mid in ids:
                    typ, mdata = M.fetch(mid, "(RFC822)")
                    if typ != "OK" or not mdata or not mdata[0]:
                        continue
                    raw = mdata[0][1]
                    msg = email.message_from_bytes(raw)
                    from_h = _decode_hdr(msg.get("From"))
                    to_h = _decode_hdr(msg.get("To"))
                    cc_h = _decode_hdr(msg.get("Cc"))
                    subj = _decode_hdr(msg.get("Subject"))
                    date_raw = msg.get("Date", "")
                    try:
                        dt = parsedate_to_datetime(date_raw)
                        date_str = dt.isoformat() if dt else date_raw
                    except Exception:
                        date_str = date_raw
                    yield {
                        "folder": folder,
                        "from": from_h,
                        "to": to_h,
                        "cc": cc_h,
                        "subject": subj,
                        "date": date_str,
                        "message_id": msg.get("Message-ID", "") or "",
                        "body": _get_body(msg),
                    }
            except Exception as e:  # noqa: BLE001 — one bad folder must not kill the run
                logger.error("fetch_mailbox: folder %s failed: %s", folder, e)
    finally:
        try:
            M.logout()
        except Exception:
            pass


def reconcile_shared_private() -> dict:
    """Fail-closed cross-mailbox reconciliation: any Message-ID that appears in a
    whole-mailbox-private box (iosif@*, daniel.wikert@) marks EVERY copy of that
    message private — including a copy sitting in a C-level box like the orb-inbound
    catch-all. Without this, a private message mirrored into a public box would be
    readable by C-level. Never adds the executive_readable tag. Idempotent.

    Cheap enough to call after each live-sync poll. Returns count upgraded."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH priv_mids AS (
                SELECT DISTINCT metadata->>'message_id' AS mid
                FROM knowledge_store
                WHERE source_type = 'imap_message'
                  AND metadata->>'mailbox_owner' = ANY(%s)
                  AND COALESCE(metadata->>'message_id', '') <> ''
            )
            UPDATE knowledge_store k
            SET is_private = true,
                tags = CASE WHEN NOT ('founder_private' = ANY(k.tags))
                            THEN array_append(k.tags, 'founder_private') ELSE k.tags END,
                metadata = jsonb_set(k.metadata, '{classification_reasons}',
                    COALESCE(k.metadata->'classification_reasons', '[]'::jsonb)
                    || '["shared-message-with-private-box"]'::jsonb)
            WHERE k.source_type = 'imap_message'
              AND k.is_private = false
              AND k.metadata->>'message_id' IN (SELECT mid FROM priv_mids WHERE mid IS NOT NULL)
            RETURNING k.id
            """,
            (list(FOUNDER_PRIVATE_MAILBOXES),),
        )
        upgraded = len(cur.fetchall())
        conn.commit()
    return {"upgraded_to_private": upgraded}


def reclassify_existing() -> dict:
    """Recompute is_private/tags/classification_reasons for every already-ingested
    imap_message row, in place. Run after tightening or widening the classifier so
    stored rows match current doctrine without a re-fetch. Read-only on IMAP (touches
    only the DB). Returns counts of rows scanned / flipped."""
    stats = {"scanned": 0, "now_private": 0, "now_public": 0, "unchanged": 0}
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, content, is_private, tags, metadata FROM knowledge_store "
                "WHERE source_type = 'imap_message'"
            )
            rows = cur.fetchall()
        for entry_id, content, was_private, _tags, metadata in rows:
            stats["scanned"] += 1
            md = metadata or {}
            owner = md.get("mailbox_owner", "")
            participants = f"{md.get('from','')} {md.get('to','')} {md.get('cc','')}"
            subject = md.get("subject") or ""
            cls = classify_email(owner, participants, subject, content or "")
            if cls["is_private"] == bool(was_private):
                stats["unchanged"] += 1
                # still refresh tags/reasons so they reflect current rules
            else:
                stats["now_private" if cls["is_private"] else "now_public"] += 1
            new_md = dict(md)
            new_md["classification_reasons"] = cls["reasons"]
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE knowledge_store SET is_private=%s, tags=%s, metadata=%s::jsonb "
                    "WHERE id=%s",
                    (cls["is_private"], cls["tags"], json.dumps(new_md), entry_id),
                )
            conn.commit()
    return stats


def ingest_mailbox(mailbox_email: str, password: str, host: str | None = None,
                   port: int | None = None, folders: list[str] | None = None,
                   search: str = "ALL", limit: int | None = None) -> dict:
    """Fetch + classify + ingest one mailbox. Returns counts. Read-only on IMAP."""
    host = host or os.environ.get("IMAP_HOST", "")
    port = port or int(os.environ.get("IMAP_PORT", "993"))
    ensure_dedup_index()

    stats = {"mailbox": mailbox_email, "fetched": 0, "inserted": 0,
             "duplicate": 0, "private": 0, "errors": 0}
    with _conn() as conn:
        for m in fetch_mailbox(host, port, mailbox_email, password,
                               folders=folders, search=search, limit=limit):
            stats["fetched"] += 1
            try:
                participants = f"{m['from']} {m['to']} {m['cc']}"
                if classify_email(mailbox_email, participants, m["subject"], m["body"])["is_private"]:
                    stats["private"] += 1
                outcome = ingest_message(
                    conn, mailbox_owner=mailbox_email, folder=m["folder"],
                    msg_from=m["from"], msg_to=m["to"], msg_cc=m["cc"],
                    subject=m["subject"], date_str=m["date"],
                    message_id=m["message_id"], body=m["body"],
                )
                stats[outcome] += 1
                conn.commit()
            except Exception as e:  # noqa: BLE001
                conn.rollback()
                stats["errors"] += 1
                logger.error("ingest_mailbox %s: message failed: %s", mailbox_email, e)
    return stats
