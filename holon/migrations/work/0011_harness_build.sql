-- 0011: H10 (phase H). The harness build that ran a result, and the build each rubric
-- judgment was made against, so pass rates group by build and a regressed build is
-- detectable from judgments alone.
--
-- The build arrives in result.usage.build (filled by the harness — e.g. operant's
-- version and memory-wire revision), recorded on the run; the judge copies the judged
-- run's build onto its judgment rows.

ALTER TABLE runs ADD COLUMN harness_build TEXT;
ALTER TABLE judgments ADD COLUMN build TEXT;

CREATE INDEX idx_judgments_build ON judgments(build);
