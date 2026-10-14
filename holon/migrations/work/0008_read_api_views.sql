-- 0008: the read API (boundary doc §4.1, work package H2).
-- SQL views over work.db that Operant (or any presentation surface) may SELECT from,
-- read-only. The api_version row says which view shapes this database publishes; a view
-- changes only with a version bump, and `holon lint` (pack api.views) proves every view
-- exists and matches it. Consumers MUST open the database with mode=ro (law 3) and refuse
-- a version they do not know.

CREATE TABLE IF NOT EXISTS api_meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT INTO api_meta (key, value) VALUES ('api_version', '1');

-- employees with their department slug and manager name
CREATE VIEW api_employees AS
SELECT e.id, e.name, e.title, d.slug AS department, e.reports_to AS manager_id,
       m.name AS manager, e.status, e.driver, e.roles
FROM employees e
JOIN departments d ON d.id = e.dept_id
LEFT JOIN employees m ON m.id = e.reports_to;

-- departments with their head and scope roots (from holon.toml [scope]; roots are
-- instance config, so the view reads them from the settings table when present)
CREATE VIEW api_departments AS
SELECT d.id, d.slug, d.name, d.mission, d.head_id, e.name AS head, d.status, d.profile
FROM departments d
LEFT JOIN employees e ON e.id = d.head_id;

-- the board: ready and running stages with their work, department, lane, lease and claimant
CREATE VIEW api_board AS
SELECT t.id, t.title, t.stage, t.status, t.lane, t.priority, t.risk,
       d.slug AS department, t.assignee, t.approver, t.grade, r.id AS run_id,
       r.lease_until, r.employee_id, r.started_at
FROM tasks t
LEFT JOIN departments d ON d.id = t.dept_id
LEFT JOIN runs r ON r.task_id = t.id AND r.status = 'running'
WHERE t.stage IS NOT NULL AND t.status IN ('ready', 'claimed');

-- pending approvals: the approve stage with the plan output that feeds it (the plan
-- stage is its one `task_needs` predecessor; the newest plan output of that node wins)
CREATE VIEW api_approvals AS
SELECT t.id, t.title, d.slug AS department, t.grade, t.approver, t.risk,
       o.body AS plan, t.created_at
FROM tasks t
LEFT JOIN departments d ON d.id = t.dept_id
LEFT JOIN outputs o ON o.task_id = (SELECT needs_id FROM task_needs WHERE task_id = t.id LIMIT 1)
     AND o.kind = 'plan'
     AND o.created_at = (SELECT MAX(o2.created_at) FROM outputs o2
                         WHERE o2.task_id = o.task_id AND o2.kind = 'plan')
WHERE t.stage = 'approve' AND t.status IN ('ready', 'claimed');

-- unread messages per recipient
CREATE VIEW api_messages AS
SELECT m.id, m.ts, m.sender, m.to_ref, m.kind, m.task_id, m.body
FROM messages m
WHERE m.acked_at IS NULL AND m.ack_required = 1;

-- routine triggers: the schedule table is today's routine surface (wake + recurring);
-- event triggers land with H7 and extend this view
CREATE VIEW api_triggers AS
SELECT s.id, 'cron' AS trigger_kind, s.cron AS pattern, d.slug AS department,
       e.name AS employee, s.enabled, s.failure_streak, s.next_run_at
FROM schedules s
LEFT JOIN employees e ON e.id = s.employee_id
LEFT JOIN departments d ON d.id = e.dept_id;
