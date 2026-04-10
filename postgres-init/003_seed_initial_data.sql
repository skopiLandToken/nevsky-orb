INSERT INTO assistant_names (canonical_name, gender_class)
VALUES
  ('Aleksandr', 'son'),
  ('Andrei', 'son'),
  ('Anton', 'son'),
  ('Dmitri', 'son'),
  ('Ivan', 'son'),
  ('Mikhail', 'son'),
  ('Nikita', 'son'),
  ('Pavel', 'son'),
  ('Sergei', 'son'),
  ('Vladimir', 'son'),
  ('Aleksandra', 'daughter'),
  ('Anastasia', 'daughter'),
  ('Anna', 'daughter'),
  ('Daria', 'daughter'),
  ('Ekaterina', 'daughter'),
  ('Elena', 'daughter'),
  ('Irina', 'daughter'),
  ('Ksenia', 'daughter'),
  ('Olga', 'daughter'),
  ('Sofia', 'daughter')
ON CONFLICT (canonical_name) DO NOTHING;

INSERT INTO users (full_name, email, role)
VALUES
  ('Iosif Skorohodov', 'iosifskorohodov@gmail.com', 'founder')
ON CONFLICT (email) DO NOTHING;

INSERT INTO user_profiles (
  user_id,
  preferred_summary_style,
  preferred_alert_intensity,
  calls_allowed_for_urgent,
  calls_allowed_for_alarm,
  acknowledgment_required_for_day_start
)
SELECT
  id,
  'short',
  'balanced',
  FALSE,
  FALSE,
  TRUE
FROM users
WHERE email = 'iosifskorohodov@gmail.com'
ON CONFLICT (user_id) DO NOTHING;

INSERT INTO personas (
  user_id,
  canonical_name,
  gender_class,
  tone_profile,
  summary_style,
  reminder_style,
  escalation_style
)
SELECT
  u.id,
  'Aleksandr',
  'son',
  'calm_direct',
  'short',
  'firm_clear',
  'measured'
FROM users u
WHERE u.email = 'iosifskorohodov@gmail.com'
ON CONFLICT (user_id) DO NOTHING;

UPDATE assistant_names
SET
  is_available = FALSE,
  is_reserved = TRUE,
  reserved_by_user_id = (SELECT id FROM users WHERE email = 'iosifskorohodov@gmail.com'),
  reserved_at = NOW()
WHERE canonical_name = 'Aleksandr'
  AND reserved_by_user_id IS NULL;
