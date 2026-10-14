-- 0009: H4 (phase D). The run row carries what the result said the run consumed:
-- tool_calls and duration_s join tokens_in, tokens_out, cost. `usage` is optional in the
-- result contract (holon/core/contract.py); a value the driver measured in its own output
-- still wins (Work.set_run_usage coalesces).

ALTER TABLE runs ADD COLUMN tool_calls INTEGER;
ALTER TABLE runs ADD COLUMN duration_s REAL;
