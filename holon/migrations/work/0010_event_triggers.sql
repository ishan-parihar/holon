-- 0010: H7 (phase F). Event triggers: a routine may fire on an event pattern
-- (routine add --on event:<pattern>) instead of a cron. An event schedule has no cron
-- ('') and no next_run_at; the runtime that sees a matching event fires it with
-- `holon routine fire`, at most once per [events] cooldown_s. Holon never matches
-- events itself.
--
-- api_version 2: same view shapes as v1; api_triggers may now carry event rows
-- (trigger_kind 'event', pattern = the event pattern). Consumers refuse versions
-- they do not know, so a surface that has not learned event rows stays off them.

ALTER TABLE schedules ADD COLUMN event TEXT;

UPDATE api_meta SET value='2' WHERE key='api_version';
DROP VIEW api_triggers;
CREATE VIEW api_triggers AS
SELECT s.id, CASE WHEN s.event IS NOT NULL THEN 'event' ELSE 'cron' END AS trigger_kind,
       coalesce(s.event, s.cron) AS pattern, d.slug AS department,
       e.name AS employee, s.enabled, s.failure_streak, s.next_run_at
FROM schedules s
LEFT JOIN employees e ON e.id = s.employee_id
LEFT JOIN departments d ON d.id = e.dept_id;
