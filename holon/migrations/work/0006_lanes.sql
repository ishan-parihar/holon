-- Lanes (docs/GENOME.md §10): how a piece of work runs, cheapest first. Stages copy their work's lane,
-- so a claim can tell kernel-run script stages from agent stages without a join.
ALTER TABLE tasks ADD COLUMN lane TEXT NOT NULL DEFAULT 'full' CHECK (lane IN ('script','fast','full'));
