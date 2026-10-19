# Records — decisions (AD) and regressions (RG)

Generated zones below are written by `genome rec emit`; never hand-edit them.
Topic → governing record notes go above the zones.

This registry is the durable half of the two-file decision memory: `docs/STATUS.md` carries
the live state of this branch (deployments, standing decisions, what is next), while the rows
below carry the immutable outcome records — ratified direction changes (AD) and closed
defects with their guards (RG) — keyed by id, with pointer links into the design records that
argue them. A row is minted when a direction is ratified or a defect's fix lands; it is never
edited afterwards (corrections arrive as new rows). When a zone is empty, the registry is
empty: the stub above stays, the zones render `(no records)`, and nothing is padded by hand.
