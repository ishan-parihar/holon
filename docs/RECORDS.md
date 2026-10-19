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

A row names its evidence: the file or command that shows the outcome, not a summary of
intent. A regression row names the guard that would catch the same defect again, so a
later reader can see whether the fix held rather than take the word of the row. The
ruling of what is a record and what is a dated design note lives in `docs/decisions/`;
this file is the index of the records themselves, and the two are not the same question.
