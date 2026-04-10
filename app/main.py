from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
import os
import json
from datetime import datetime, timezone, timedelta
import psycopg
from anthropic import Anthropic

app = FastAPI(title="Nevsky API", version="0.1.0")

def get_db_connection():
    return psycopg.connect(
        host="postgres",
        dbname=os.getenv("POSTGRES_DB", "nevsky_dev"),
        user=os.getenv("POSTGRES_USER", "nevsky"),
        password=os.getenv("POSTGRES_PASSWORD", "change_me_now"),
    )

def get_default_user_id(cur):
    cur.execute(
        "SELECT id FROM users WHERE email = %s LIMIT 1",
        ("iosifskorohodov@gmail.com",),
    )
    row = cur.fetchone()
    return row[0] if row else None

def write_audit_log(
    cur,
    user_id,
    action_type,
    object_type,
    object_id=None,
    event_id=None,
    recommendation_summary=None,
    human_decision=None,
    execution_status="success",
    error_message=None,
):
    cur.execute(
        """
        INSERT INTO audit_logs (
            tenant_id,
            user_id,
            event_id,
            action_type,
            object_type,
            object_id,
            recommendation_summary,
            human_decision,
            execution_status,
            error_message
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            "skopi",
            user_id,
            event_id,
            action_type,
            object_type,
            object_id,
            recommendation_summary,
            human_decision,
            execution_status,
            error_message,
        ),
    )

class HealthResponse(BaseModel):
    status: str
    environment: str

class TaskEstimateRequest(BaseModel):
    minutes: int

class ReminderSnoozeRequest(BaseModel):
    minutes: int

class SummarizeRequest(BaseModel):
    text: str

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(
        status="ok",
        environment=os.getenv("ENVIRONMENT", "unknown"),
    )

@app.get("/ready")
def ready():
    return {"ready": True}

@app.get("/today")
def today():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, title, category, status, priority, estimated_duration_minutes, created_at
                FROM tasks
                WHERE user_id = %s
                  AND status NOT IN ('completed', 'canceled', 'archived')
                ORDER BY created_at DESC
                LIMIT 20
                """,
                (user_id,),
            )
            tasks = cur.fetchall()

            cur.execute(
                """
                SELECT id, title, category, status, next_trigger_at, created_at
                FROM reminders
                WHERE user_id = %s
                  AND status NOT IN ('completed', 'canceled')
                ORDER BY next_trigger_at ASC NULLS LAST, created_at DESC
                LIMIT 20
                """,
                (user_id,),
            )
            reminders = cur.fetchall()

    return {
        "ok": True,
        "date": datetime.now(timezone.utc).date().isoformat(),
        "tasks": [
            {
                "id": str(row[0]),
                "title": row[1],
                "category": row[2],
                "status": row[3],
                "priority": row[4],
                "estimated_duration_minutes": row[5],
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in tasks
        ],
        "reminders": [
            {
                "id": str(row[0]),
                "title": row[1],
                "category": row[2],
                "status": row[3],
                "next_trigger_at": row[4].isoformat() if row[4] else None,
                "created_at": row[5].isoformat() if row[5] else None,
            }
            for row in reminders
        ],
    }

@app.post("/daily-plans/generate")
def generate_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, title, COALESCE(estimated_duration_minutes, 30)
                FROM tasks
                WHERE user_id = %s
                  AND status NOT IN ('completed', 'canceled', 'archived')
                ORDER BY created_at DESC
                LIMIT 10
                """,
                (user_id,),
            )
            tasks = cur.fetchall()

            summary = f"Daily plan for {plan_date.isoformat()} with {len(tasks)} task(s)."

            cur.execute(
                """
                INSERT INTO daily_plans (
                    tenant_id,
                    user_id,
                    plan_date,
                    summary,
                    status,
                    created_at,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s, NOW(), NOW())
                ON CONFLICT (user_id, plan_date)
                DO UPDATE SET
                    summary = EXCLUDED.summary,
                    status = 'draft',
                    accepted_at = NULL,
                    started_at = NULL,
                    updated_at = NOW()
                RETURNING id
                """,
                ("skopi", user_id, plan_date, summary, "draft"),
            )
            daily_plan_id = cur.fetchone()[0]

            cur.execute("DELETE FROM daily_plan_items WHERE daily_plan_id = %s", (daily_plan_id,))

            for idx, task in enumerate(tasks, start=1):
                cur.execute(
                    """
                    INSERT INTO daily_plan_items (
                        daily_plan_id,
                        task_id,
                        position,
                        item_type,
                        title,
                        short_note,
                        estimated_duration_minutes,
                        accepted_duration_minutes,
                        status,
                        created_at,
                        updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    """,
                    (
                        daily_plan_id,
                        task[0],
                        idx,
                        "task",
                        task[1],
                        "Auto-generated from open task list",
                        task[2],
                        None,
                        "planned",
                    ),
                )

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="daily_plan.generated",
                object_type="daily_plan",
                object_id=daily_plan_id,
                recommendation_summary=summary,
                human_decision="generated",
            )

        conn.commit()

    return {
        "ok": True,
        "daily_plan_id": str(daily_plan_id),
        "plan_date": plan_date.isoformat(),
        "task_count": len(tasks),
        "summary": summary,
    }

@app.get("/daily-plans/today")
def get_today_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, plan_date, summary, status, accepted_at, started_at, created_at
                FROM daily_plans
                WHERE user_id = %s AND plan_date = %s
                LIMIT 1
                """,
                (user_id, plan_date),
            )
            plan = cur.fetchone()

            if not plan:
                return {"ok": True, "plan": None, "items": []}

            cur.execute(
                """
                SELECT id, position, item_type, title, short_note,
                       estimated_duration_minutes, accepted_duration_minutes, status, task_id
                FROM daily_plan_items
                WHERE daily_plan_id = %s
                ORDER BY position ASC
                """,
                (plan[0],),
            )
            items = cur.fetchall()

    return {
        "ok": True,
        "plan": {
            "id": str(plan[0]),
            "plan_date": plan[1].isoformat() if plan[1] else None,
            "summary": plan[2],
            "status": plan[3],
            "accepted_at": plan[4].isoformat() if plan[4] else None,
            "started_at": plan[5].isoformat() if plan[5] else None,
            "created_at": plan[6].isoformat() if plan[6] else None,
        },
        "items": [
            {
                "id": str(row[0]),
                "position": row[1],
                "item_type": row[2],
                "title": row[3],
                "short_note": row[4],
                "estimated_duration_minutes": row[5],
                "accepted_duration_minutes": row[6],
                "status": row[7],
                "task_id": str(row[8]) if row[8] else None,
            }
            for row in items
        ],
    }

@app.post("/daily-plans/accept")
def accept_today_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE daily_plans
                SET status = 'accepted',
                    accepted_at = NOW(),
                    updated_at = NOW()
                WHERE user_id = %s AND plan_date = %s
                RETURNING id, plan_date, status, accepted_at, started_at
                """,
                (user_id, plan_date),
            )
            plan = cur.fetchone()

            if not plan:
                raise HTTPException(status_code=404, detail="No daily plan found for today")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="daily_plan.accepted",
                object_type="daily_plan",
                object_id=plan[0],
                human_decision="accepted",
            )

        conn.commit()

    return {
        "ok": True,
        "plan": {
            "id": str(plan[0]),
            "plan_date": plan[1].isoformat() if plan[1] else None,
            "status": plan[2],
            "accepted_at": plan[3].isoformat() if plan[3] else None,
            "started_at": plan[4].isoformat() if plan[4] else None,
        },
    }

@app.post("/daily-plans/start")
def start_today_daily_plan():
    plan_date = datetime.now(timezone.utc).date()

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE daily_plans
                SET status = 'started',
                    started_at = NOW(),
                    updated_at = NOW()
                WHERE user_id = %s AND plan_date = %s
                RETURNING id, plan_date, status, accepted_at, started_at
                """,
                (user_id, plan_date),
            )
            plan = cur.fetchone()

            if not plan:
                raise HTTPException(status_code=404, detail="No daily plan found for today")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="daily_plan.started",
                object_type="daily_plan",
                object_id=plan[0],
                human_decision="started",
            )

        conn.commit()

    return {
        "ok": True,
        "plan": {
            "id": str(plan[0]),
            "plan_date": plan[1].isoformat() if plan[1] else None,
            "status": plan[2],
            "accepted_at": plan[3].isoformat() if plan[3] else None,
            "started_at": plan[4].isoformat() if plan[4] else None,
        },
    }

@app.post("/tasks/{task_id}/estimate")
def estimate_task(task_id: str, body: TaskEstimateRequest):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE tasks
                SET estimated_duration_minutes = %s,
                    requested_duration_input = FALSE,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, estimated_duration_minutes
                """,
                (body.minutes, task_id, user_id),
            )
            task = cur.fetchone()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            cur.execute(
                """
                UPDATE daily_plan_items dpi
                SET accepted_duration_minutes = %s,
                    updated_at = NOW()
                FROM daily_plans dp
                WHERE dpi.daily_plan_id = dp.id
                  AND dpi.task_id = %s
                  AND dp.user_id = %s
                  AND dp.plan_date = %s
                """,
                (body.minutes, task_id, user_id, datetime.now(timezone.utc).date()),
            )

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="task.estimated",
                object_type="task",
                object_id=task[0],
                human_decision=f"estimated_{body.minutes}_minutes",
            )

        conn.commit()

    return {
        "ok": True,
        "task": {
            "id": str(task[0]),
            "title": task[1],
            "estimated_duration_minutes": task[2],
        },
    }

@app.post("/tasks/{task_id}/complete")
def complete_task(task_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE tasks
                SET status = 'completed',
                    actual_duration_minutes = COALESCE(actual_duration_minutes, estimated_duration_minutes),
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, status, actual_duration_minutes
                """,
                (task_id, user_id),
            )
            task = cur.fetchone()

            if not task:
                raise HTTPException(status_code=404, detail="Task not found")

            cur.execute(
                """
                UPDATE daily_plan_items dpi
                SET status = 'completed',
                    updated_at = NOW()
                FROM daily_plans dp
                WHERE dpi.daily_plan_id = dp.id
                  AND dpi.task_id = %s
                  AND dp.user_id = %s
                  AND dp.plan_date = %s
                """,
                (task_id, user_id, datetime.now(timezone.utc).date()),
            )

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="task.completed",
                object_type="task",
                object_id=task[0],
                human_decision="completed",
            )

        conn.commit()

    return {
        "ok": True,
        "task": {
            "id": str(task[0]),
            "title": task[1],
            "status": task[2],
            "actual_duration_minutes": task[3],
        },
    }

@app.post("/reminders/{reminder_id}/ack")
def acknowledge_reminder(reminder_id: str):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                UPDATE reminders
                SET status = 'completed',
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, status
                """,
                (reminder_id, user_id),
            )
            reminder = cur.fetchone()

            if not reminder:
                raise HTTPException(status_code=404, detail="Reminder not found")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="reminder.acknowledged",
                object_type="reminder",
                object_id=reminder[0],
                human_decision="acknowledged",
            )

        conn.commit()

    return {
        "ok": True,
        "reminder": {
            "id": str(reminder[0]),
            "title": reminder[1],
            "status": reminder[2],
        },
    }

@app.post("/reminders/{reminder_id}/snooze")
def snooze_reminder(reminder_id: str, body: ReminderSnoozeRequest):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)
            next_trigger_at = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)

            cur.execute(
                """
                UPDATE reminders
                SET status = 'pending',
                    next_trigger_at = %s,
                    updated_at = NOW()
                WHERE id = %s AND user_id = %s
                RETURNING id, title, status, next_trigger_at
                """,
                (next_trigger_at, reminder_id, user_id),
            )
            reminder = cur.fetchone()

            if not reminder:
                raise HTTPException(status_code=404, detail="Reminder not found")

            write_audit_log(
                cur,
                user_id=user_id,
                action_type="reminder.snoozed",
                object_type="reminder",
                object_id=reminder[0],
                human_decision=f"snoozed_{body.minutes}_minutes",
            )

        conn.commit()

    return {
        "ok": True,
        "reminder": {
            "id": str(reminder[0]),
            "title": reminder[1],
            "status": reminder[2],
            "next_trigger_at": reminder[3].isoformat() if reminder[3] else None,
        },
    }

@app.get("/audit-logs")
def get_audit_logs():
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)

            cur.execute(
                """
                SELECT id, action_type, object_type, object_id, human_decision, execution_status, created_at
                FROM audit_logs
                WHERE user_id = %s
                ORDER BY created_at DESC
                LIMIT 50
                """,
                (user_id,),
            )
            rows = cur.fetchall()

    return {
        "ok": True,
        "logs": [
            {
                "id": str(row[0]),
                "action_type": row[1],
                "object_type": row[2],
                "object_id": str(row[3]) if row[3] else None,
                "human_decision": row[4],
                "execution_status": row[5],
                "created_at": row[6].isoformat() if row[6] else None,
            }
            for row in rows
        ],
    }

@app.post("/ai/summarize")
def ai_summarize(body: SummarizeRequest):
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise HTTPException(status_code=500, detail="ANTHROPIC_API_KEY is not set")

    client = Anthropic(api_key=api_key)

    trimmed_text = body.text.strip()[:4000]

    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=120,
        temperature=0,
        system="You are Nevsky ORB. Summarize operational text briefly and clearly in 2-4 bullet points.",
        messages=[
            {
                "role": "user",
                "content": f"Summarize this text for an operational dashboard:\\n\\n{trimmed_text}"
            }
        ]
    )

    summary_text = ""
    if response.content and len(response.content) > 0:
        summary_text = response.content[0].text

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            user_id = get_default_user_id(cur)
            write_audit_log(
                cur,
                user_id=user_id,
                action_type="ai.summarized",
                object_type="ai_request",
                recommendation_summary=summary_text,
                human_decision="anthropic_summary",
            )
        conn.commit()

    return {
        "ok": True,
        "model": "claude-3-5-haiku-latest",
        "summary": summary_text,
        "input_chars": len(trimmed_text),
    }

@app.post("/ingest/telegram-update")
async def ingest_telegram_update(request: Request):
    payload = await request.json()

    message = payload.get("message", {}) or {}
    text = (message.get("text") or "").strip()
    update_id = payload.get("update_id")

    summary = f"Telegram update {update_id}"
    if text:
        summary = f"Telegram message: {text[:200]}"

    task_created = False
    task_id = None
    reminder_created = False
    reminder_id = None
    owner_user_id = None

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            owner_user_id = get_default_user_id(cur)

            cur.execute(
                """
                INSERT INTO events (
                    tenant_id,
                    user_id,
                    source_type,
                    event_type,
                    priority,
                    approval_required,
                    summary,
                    normalized_payload_json,
                    occurred_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (
                    "skopi",
                    owner_user_id,
                    "telegram",
                    "message.telegram.received",
                    "normal",
                    False,
                    summary,
                    json.dumps(payload),
                    datetime.now(timezone.utc),
                ),
            )
            event_id = cur.fetchone()[0]

            if text.lower().startswith("/task "):
                title = text[6:].strip()
                if title:
                    cur.execute(
                        """
                        INSERT INTO tasks (
                            tenant_id,
                            user_id,
                            title,
                            description,
                            category,
                            status,
                            priority,
                            source_event_id,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        RETURNING id
                        """,
                        (
                            "skopi",
                            owner_user_id,
                            title,
                            f"Created from Telegram update {update_id}",
                            "telegram",
                            "created",
                            "normal",
                            event_id,
                        ),
                    )
                    task_id = cur.fetchone()[0]
                    task_created = True

                    write_audit_log(
                        cur,
                        user_id=owner_user_id,
                        event_id=event_id,
                        action_type="task.created",
                        object_type="task",
                        object_id=task_id,
                        human_decision="created_from_telegram",
                    )

            elif text.lower().startswith("/remind "):
                title = text[8:].strip()
                if title:
                    trigger_at = datetime.now(timezone.utc) + timedelta(hours=1)
                    cur.execute(
                        """
                        INSERT INTO reminders (
                            tenant_id,
                            user_id,
                            title,
                            category,
                            status,
                            initial_trigger_at,
                            next_trigger_at,
                            ack_required,
                            created_at,
                            updated_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                        RETURNING id
                        """,
                        (
                            "skopi",
                            owner_user_id,
                            title,
                            "telegram",
                            "pending",
                            trigger_at,
                            trigger_at,
                            False,
                        ),
                    )
                    reminder_id = cur.fetchone()[0]
                    reminder_created = True

                    write_audit_log(
                        cur,
                        user_id=owner_user_id,
                        event_id=event_id,
                        action_type="reminder.created",
                        object_type="reminder",
                        object_id=reminder_id,
                        human_decision="created_from_telegram",
                    )

        conn.commit()

    return {
        "ok": True,
        "source": "telegram",
        "event_id": str(event_id),
        "task_created": task_created,
        "task_id": str(task_id) if task_id else None,
        "reminder_created": reminder_created,
        "reminder_id": str(reminder_id) if reminder_id else None,
        "received_at": datetime.now(timezone.utc).isoformat(),
        "top_level_keys": list(payload.keys()),
    }
