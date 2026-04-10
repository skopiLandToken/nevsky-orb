CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    full_name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT,
    telegram_user_id TEXT UNIQUE,
    role TEXT,
    timezone TEXT NOT NULL DEFAULT 'America/Los_Angeles',
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS assistant_names (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    canonical_name TEXT NOT NULL UNIQUE,
    gender_class TEXT NOT NULL CHECK (gender_class IN ('son', 'daughter')),
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    is_reserved BOOLEAN NOT NULL DEFAULT FALSE,
    is_retired BOOLEAN NOT NULL DEFAULT FALSE,
    reserved_by_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    reserved_at TIMESTAMPTZ,
    retired_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS personas (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    canonical_name TEXT NOT NULL UNIQUE,
    gender_class TEXT NOT NULL CHECK (gender_class IN ('son', 'daughter')),
    tone_profile TEXT,
    summary_style TEXT,
    reminder_style TEXT,
    escalation_style TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_profiles (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id UUID NOT NULL UNIQUE REFERENCES users(id) ON DELETE CASCADE,
    preferred_summary_style TEXT,
    preferred_alert_intensity TEXT,
    calls_allowed_for_urgent BOOLEAN NOT NULL DEFAULT FALSE,
    calls_allowed_for_alarm BOOLEAN NOT NULL DEFAULT FALSE,
    quiet_hours_start TIME,
    quiet_hours_end TIME,
    default_day_start_time TIME,
    preferred_day_briefing_time TIME,
    acknowledgment_required_for_day_start BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS contacts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    full_name TEXT NOT NULL,
    company TEXT,
    role_title TEXT,
    email TEXT,
    phone TEXT,
    relationship_stage TEXT,
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    owner_user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    priority TEXT,
    target_date DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    source_type TEXT NOT NULL,
    event_type TEXT NOT NULL,
    priority TEXT,
    urgency_score NUMERIC(5,2),
    approval_required BOOLEAN NOT NULL DEFAULT FALSE,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    contact_id UUID REFERENCES contacts(id) ON DELETE SET NULL,
    task_id UUID,
    reminder_id UUID,
    summary TEXT,
    raw_payload_ref TEXT,
    normalized_payload_json JSONB,
    emotional_signal_label TEXT,
    emotional_signal_score NUMERIC(5,2),
    emotional_signal_confidence NUMERIC(5,2),
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    priority TEXT,
    urgency TEXT,
    estimated_duration_minutes INTEGER,
    actual_duration_minutes INTEGER,
    requested_duration_input BOOLEAN NOT NULL DEFAULT FALSE,
    due_at TIMESTAMPTZ,
    scheduled_for_date DATE,
    scheduled_start_at TIMESTAMPTZ,
    scheduled_end_at TIMESTAMPTZ,
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    source_event_id UUID REFERENCES events(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS daily_plans (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    plan_date DATE NOT NULL,
    summary TEXT,
    status TEXT NOT NULL DEFAULT 'draft',
    accepted_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, plan_date)
);

CREATE TABLE IF NOT EXISTS daily_plan_items (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    daily_plan_id UUID NOT NULL REFERENCES daily_plans(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    position INTEGER NOT NULL,
    item_type TEXT NOT NULL DEFAULT 'task',
    title TEXT NOT NULL,
    short_note TEXT,
    estimated_duration_minutes INTEGER,
    accepted_duration_minutes INTEGER,
    status TEXT NOT NULL DEFAULT 'planned',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS reminders (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    initial_trigger_at TIMESTAMPTZ,
    next_trigger_at TIMESTAMPTZ,
    ack_required BOOLEAN NOT NULL DEFAULT FALSE,
    escalation_policy_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS approvals (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id TEXT NOT NULL DEFAULT 'skopi',
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    approval_type TEXT NOT NULL,
    target_object_type TEXT NOT NULL,
    target_object_id UUID,
    status TEXT NOT NULL DEFAULT 'requested',
    requested_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    approved_at TIMESTAMPTZ,
    rejected_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS app_skopi_events (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_key TEXT UNIQUE,
    event_type TEXT NOT NULL,
    source_entity_type TEXT,
    source_entity_id TEXT,
    raw_payload_json JSONB NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ,
    linked_event_id UUID REFERENCES events(id) ON DELETE SET NULL,
    processing_status TEXT NOT NULL DEFAULT 'received'
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_contacts_email ON contacts(email);
CREATE INDEX IF NOT EXISTS idx_events_event_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_user_id ON events(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_user_id ON tasks(user_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_reminders_user_id ON reminders(user_id);
CREATE INDEX IF NOT EXISTS idx_approvals_user_id ON approvals(user_id);
CREATE INDEX IF NOT EXISTS idx_app_skopi_events_event_type ON app_skopi_events(event_type);
