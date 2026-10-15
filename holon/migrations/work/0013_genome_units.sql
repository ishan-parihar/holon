-- 0013: the genome layer (U1/K-4): unit manifests, synced into the database, published as
-- read API v3. A unit (a service the organization runs on, or a department's own genome) is
-- declared in $HOLON_HOME/genome/units/<slug>.toml — charter, tier, roots, the services it
-- offers with their entry points, what it consumes ("unit:service"), and the departments that
-- pool it. `holon genome sync` regenerates these rows; `holon lint` (bind.service, bind.pool)
-- checks the references; `holon genome emit` derives registry.md from them.
--
-- api_version 3: adds api_units, api_services and api_memory. api_memory reads memory.db —
-- attach it read-only first: ATTACH DATABASE 'file:<memory.db>?mode=ro' AS memory.

CREATE TABLE units (
  slug      TEXT PRIMARY KEY,
  charter   TEXT,
  category  TEXT,
  tier      TEXT,
  roots     TEXT,            -- JSON array: read-scope roots granted to pooling departments
  synced_at TEXT NOT NULL
);

CREATE TABLE unit_services (
  slug    TEXT NOT NULL,
  service TEXT NOT NULL,
  command TEXT,
  accepts TEXT,
  returns TEXT,
  PRIMARY KEY (slug, service)
);

CREATE TABLE unit_edges (
  slug  TEXT NOT NULL,
  kind  TEXT NOT NULL CHECK (kind IN ('pool', 'consumes')),
  other TEXT NOT NULL,      -- pool: a department slug; consumes: "unit:service"
  PRIMARY KEY (slug, kind, other)
);

UPDATE api_meta SET value='3' WHERE key='api_version';

CREATE VIEW api_units AS
SELECT slug, charter, category, tier, synced_at FROM units;

CREATE VIEW api_services AS
SELECT slug AS unit, service, command, accepts, returns FROM unit_services;
