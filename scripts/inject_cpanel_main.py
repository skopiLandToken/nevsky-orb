"""Atomic injector for cPanel delete-approval wiring in app/main.py.

Three edits, each guarded to match exactly once (abort otherwise):
  1. fire_pending_cpanel_delete_cards() sweeper (next to the founder-private sweeper)
  2. cpanel_del_approve / cpanel_del_deny callback handler (after the lilith_priv block)
  3. call the sweeper after each Sophia reply path (unified webhook + sophia_webhook)
"""
import sys
import py_compile

P = "app/main.py"
src = open(P).read()
orig = src


def repl(s, old, new, label):
    n = s.count(old)
    if n != 1:
        print(f"ABORT: anchor '{label}' matched {n} times (need 1)")
        sys.exit(1)
    return s.replace(old, new)


# ── 1) Sweeper, placed right before the YINDO BRIDGE HELPERS section ──────────
sweeper = '''
async def fire_pending_cpanel_delete_cards():
    """Sweep cpanel_pending_deletes for pending rows still owing Iosif an approval
    card and send each. DESTRUCTIVE-OP GATE (DOCTRINE-MAILBOX-PROVISIONING-01):
    Sophia's delete tool only enqueues — the mailbox is removed solely on Iosif's tap.
    Mirrors fire_pending_founder_private_cards: card_sent decouples the engine write
    from this async send, and we mark sent only on confirmed Telegram delivery so a
    dropped card retries on the next Sophia turn."""
    iosif_id = os.environ.get("IOSIF_TELEGRAM_ID", "7583693994")
    try:
        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, target_email, requested_by
                       FROM cpanel_pending_deletes
                       WHERE status='pending' AND card_sent=false
                       ORDER BY requested_at ASC"""
                )
                rows = cur.fetchall()
    except Exception as e:
        print(f"[cpanel_delete] fetch pending error: {e}")
        return

    def _md_safe(s):
        return "".join(" " if c in "_*`[" else c for c in str(s or ""))

    for row in rows:
        pid, target_email, requested_by = row[0], row[1], row[2]
        text = (
            "\\U0001f5d1\\ufe0f *Mailbox Deletion \\u2014 Founder Approval Required*\\n\\n"
            "DESTRUCTIVE. This permanently removes the mailbox and its stored mail.\\n\\n"
            f"\\U0001f4ed Mailbox: *{_md_safe(target_email)}*\\n"
            f"\\U0001f64b Requested by: {_md_safe(requested_by)}\\n\\n"
            "Approve \\u2192 the mailbox is deleted on GreenGeeks now.\\n"
            "Deny \\u2192 nothing is deleted."
        )
        markup = {"inline_keyboard": [[
            {"text": "\\u2705 Approve delete", "callback_data": f"cpanel_del_approve:{pid}"},
            {"text": "\\U0001f6ab Deny", "callback_data": f"cpanel_del_deny:{pid}"},
        ]]}
        try:
            resp = await send_sophia_message_with_markup(int(iosif_id), text, markup)
            if isinstance(resp, dict) and resp.get("ok"):
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE cpanel_pending_deletes SET card_sent=true WHERE id=%s",
                            (pid,),
                        )
                        conn.commit()
            else:
                print(f"[cpanel_delete] card NOT delivered (id={pid}); will retry. resp={resp}")
        except Exception as e:
            print(f"[cpanel_delete] card send error (id={pid}): {e}")


'''
src = repl(src,
           "\n\n# === YINDO BRIDGE HELPERS (2026-05-27) ===",
           "\n" + sweeper + "\n# === YINDO BRIDGE HELPERS (2026-05-27) ===",
           "sweeper insertion point")

# ── 2) Callback handler, after the lilith_priv deny return ────────────────────
handler = '''        return {"ok": True, "action": "denied", "request_id": str(req_id)}

    # \\u2500\\u2500 cPanel mailbox delete approval (DOCTRINE-MAILBOX-PROVISIONING-01) \\u2500\\u2500
    # Iosif taps Approve/Deny on the Sophia delete card. data = cpanel_del_(approve|deny):<id>.
    if action in ("cpanel_del_approve", "cpanel_del_deny"):
        pid = reminder_id
        if not pid:
            if callback_query_id:
                await answer_telegram_callback_query(callback_query_id, "No pending id.")
            return {"ok": False, "reason": "missing_pending_id"}
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT target_email, status FROM cpanel_pending_deletes WHERE id=%s",
                        (pid,),
                    )
                    row = cur.fetchone()
        except Exception as _e:
            print(f"[cpanel_del] load error: {_e}")
            row = None
        if not row:
            if callback_query_id:
                await answer_telegram_callback_query(callback_query_id, "Request not found.")
            return {"ok": False, "reason": "pending_not_found"}
        target_email, status = row[0], row[1]
        if status != "pending":
            if callback_query_id:
                await answer_telegram_callback_query(callback_query_id, f"Already {status}.")
            return {"ok": True, "already": status}
        from_user = callback_query.get("from", {}) or {}
        decider = str(from_user.get("id", ""))

        if action == "cpanel_del_approve":
            ok, err = True, None
            try:
                from integrations.cpanel import delete_mailbox
                delete_mailbox(target_email, invoked_by="iosif")
            except Exception as _de:
                ok, err = False, str(_de)
                print(f"[cpanel_del_approve] delete error: {_de}")
            try:
                with get_db_connection() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """UPDATE cpanel_pending_deletes
                               SET status=%s, decided_at=now(), decided_by=%s, error_message=%s
                               WHERE id=%s""",
                            ("executed" if ok else "failed", decider, err, pid),
                        )
                        conn.commit()
            except Exception as _ue:
                print(f"[cpanel_del_approve] status update error: {_ue}")
            if chat_id:
                if ok:
                    await send_sophia_message(chat_id, f"\\u2705 Deleted *{target_email}* from GreenGeeks. (Audit-logged.)")
                else:
                    await send_sophia_message(chat_id, f"\\u26a0\\ufe0f Delete of *{target_email}* FAILED: {err}. Nothing was removed \\u2014 check the logs.")
            if callback_query_id:
                await answer_telegram_callback_query(callback_query_id, "Deleted \\u2705" if ok else "Delete failed \\u26a0\\ufe0f")
            return {"ok": ok, "action": "cpanel_deleted", "email": target_email}

        # deny
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE cpanel_pending_deletes
                           SET status='denied', decided_at=now(), decided_by=%s WHERE id=%s""",
                        (decider, pid),
                    )
                    conn.commit()
        except Exception as _e:
            print(f"[cpanel_del_deny] update error: {_e}")
        if chat_id:
            await send_sophia_message(chat_id, f"\\U0001f6ab Deletion of *{target_email}* denied \\u2014 nothing removed.")
        if callback_query_id:
            await answer_telegram_callback_query(callback_query_id, "Denied \\U0001f6ab")
        return {"ok": True, "action": "cpanel_delete_denied", "email": target_email}
'''
src = repl(src,
           '        return {"ok": True, "action": "denied", "request_id": str(req_id)}',
           handler,
           "lilith_priv deny return (callback handler anchor)")

# ── 3a) Sweeper call in the unified webhook Sophia path ───────────────────────
src = repl(src,
           '            print(f"[unified_webhook sophia] engine error: {_engine_err}")\n'
           '            await send_sophia_message(chat_id, "Sophia hit an internal error. Yakov has been logged.")\n'
           '        return {"ok": True}',
           '            print(f"[unified_webhook sophia] engine error: {_engine_err}")\n'
           '            await send_sophia_message(chat_id, "Sophia hit an internal error. Yakov has been logged.")\n'
           '        try:\n'
           '            await fire_pending_cpanel_delete_cards()\n'
           '        except Exception as _cp_err:\n'
           '            print(f"[unified_webhook sophia] cpanel card sweep error: {_cp_err}")\n'
           '        return {"ok": True}',
           "unified webhook sophia return")

# ── 3b) Sweeper call in the dedicated sophia_webhook path ─────────────────────
src = repl(src,
           '            print(f"[sophia_webhook] engine error: {_engine_err}")\n'
           '            await send_sophia_message(chat_id, "Sophia hit an internal error. Yakov has been logged.")\n'
           '    return {"ok": True}\n'
           '    # === END ENGINE ROUTING (sophia) ===',
           '            print(f"[sophia_webhook] engine error: {_engine_err}")\n'
           '            await send_sophia_message(chat_id, "Sophia hit an internal error. Yakov has been logged.")\n'
           '    try:\n'
           '        await fire_pending_cpanel_delete_cards()\n'
           '    except Exception as _cp_err:\n'
           '        print(f"[sophia_webhook] cpanel card sweep error: {_cp_err}")\n'
           '    return {"ok": True}\n'
           '    # === END ENGINE ROUTING (sophia) ===',
           "sophia_webhook return")

assert src != orig
open(P, "w").write(src)
print("main.py: all edits applied")
py_compile.compile(P, doraise=True)
print("py_compile OK")
