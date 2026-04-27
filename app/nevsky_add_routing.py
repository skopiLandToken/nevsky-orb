#!/usr/bin/env python3
"""
nevsky_add_routing.py — Auto-patch main.py to add a new child's Telegram routing
Usage: python3 nevsky_add_routing.py ChildName telegram_id
"""
import sys

if len(sys.argv) < 3:
    print("Usage: python3 nevsky_add_routing.py childname telegram_id")
    sys.exit(1)

child_lower = sys.argv[1].lower()
child_name = child_lower.capitalize()
telegram_id = sys.argv[2]

with open('/opt/nevsky-dev/app/main.py', 'r') as f:
    content = f.read()

# Add import
import_line = f"from children.{child_lower}_child import is_user as is_{child_lower}, ask_{child_lower}, get_intro as get_{child_lower}_intro\n"
anchor = "from children.sophia_child import is_iosif, ask_sophia, get_intro as get_sophia_intro\n"

if import_line not in content:
    content = content.replace(anchor, anchor + import_line)
    print(f"✅ Import added for {child_name}")

# Add routing block
routing_block = f'''    # ── {child_name.upper()} — personal ORB child ────────────────────────────
    if chat_id and is_{child_lower}(sender_telegram_id):
        if text.lower() in ["/start", "hi", "hello", ""]:
            await send_telegram_message(chat_id, get_{child_lower}_intro(), reply_markup=None)
        else:
            reply = await ask_{child_lower}(user_message=text)
            await send_telegram_message(chat_id, reply)
        return {{"ok": True}}
    # ── END {child_name.upper()} ─────────────────────────────────────────────

    result = await process_telegram_payload(payload)'''

old_anchor = "    result = await process_telegram_payload(payload)"

if f"is_{child_lower}(sender_telegram_id)" not in content:
    content = content.replace(
        "    # ── END SOPHIA ────────────────────────────────────────────────────\n\n    result = await process_telegram_payload(payload)",
        f"    # ── END SOPHIA ────────────────────────────────────────────────────\n\n{routing_block}"
    )
    print(f"✅ Routing wired for {child_name}")

with open('/opt/nevsky-dev/app/main.py', 'w') as f:
    f.write(content)

print(f"\n✅ {child_name} fully wired. Restart API to activate:")
print("   docker compose restart api")
