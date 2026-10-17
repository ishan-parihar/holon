-- 0015: surfaces and the service contract (C1/C2 — the ventures import's pre-pilot package;
-- the C-track, revised by the 2026-10-16 red-team audit). read API v4, additive: no column is
-- reshaped or removed, so a v3-era consumer reading by name sees the same rows as before; the
-- new columns are appended and api_pool is a new view.
--
-- A unit manifest gains what the capability layer needs: `category = "surface"` marks a
-- harness-level surface (browser, channels, mcp, tunnel) rather than a foundation service;
-- `owner` names the department that keeps a surface true; and a service may declare its
-- contract — effect (read | write), note (when to prefer this route), args, output, health
-- (a command probing the route's liveness) — so a brief can say which route to prefer and
-- which is down, and api_pool publishes the pool edges a runtime indexes (R-04).

ALTER TABLE units ADD COLUMN owner TEXT;            -- the department that keeps this unit true (C4)

ALTER TABLE unit_services ADD COLUMN effect TEXT;   -- read | write: what calling it does
ALTER TABLE unit_services ADD COLUMN note TEXT;     -- when to prefer this route (the anti-waste line)
ALTER TABLE unit_services ADD COLUMN args TEXT;     -- the call shape, for a runtime's tool index
ALTER TABLE unit_services ADD COLUMN output TEXT;   -- toon | json | text
ALTER TABLE unit_services ADD COLUMN health TEXT;   -- a command probing this route's liveness

UPDATE api_meta SET value='4' WHERE key='api_version';

DROP VIEW IF EXISTS api_units;
CREATE VIEW api_units AS
SELECT slug, charter, category, tier, owner, synced_at FROM units;

DROP VIEW IF EXISTS api_services;
CREATE VIEW api_services AS
SELECT slug AS unit, service, command, accepts, returns, effect, args, output, note, health
FROM unit_services;

-- the pool edges a runtime indexes (R-04): which department may use which unit. "*" is an
-- org-wide surface — every entitled brief, including department-less work.
CREATE VIEW api_pool AS
SELECT e.slug AS unit, e.other AS dept FROM unit_edges e WHERE e.kind='pool';
