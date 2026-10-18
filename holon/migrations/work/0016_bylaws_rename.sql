-- 0016: the department genome is the department bylaws. The holon layer took the name bylaws so that "genome" means
-- one thing (project-genome). Only the column name changes; the JSON snapshot in it is untouched.
ALTER TABLE departments RENAME COLUMN genome TO bylaws;
