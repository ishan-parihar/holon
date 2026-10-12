-- Roles (genome) on employees; a genome profile on every department.
ALTER TABLE employees ADD COLUMN roles TEXT;        -- JSON list of standard roles; NULL = inferred from duties
ALTER TABLE departments ADD COLUMN profile TEXT;    -- genome profile name
ALTER TABLE departments ADD COLUMN genome TEXT;     -- JSON snapshot of the profile's slots when it was applied
