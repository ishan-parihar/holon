# Page index — every active doc, the question it owns

This index answers one question: which document holds the answer to what. Every row
below names a file and the single question that file owns, so a reader arriving with a
question can go straight to the one document that answers it instead of opening five and
guessing. A file that is not listed here is either new and unclassified, or deliberately
out of scope. When a document is superseded it moves to `docs/archive/` with a tombstone
at its old path, and this index is updated to point at the archive rather than keeping a
row for a file that no longer holds a live answer.

The distinction that matters most here is between a law and a record. `BYLAWS.md` and
`CONTRACT.md` state rules that are enforced by tooling; a `design/` record states what
was decided on a date and why, and is frozen once written. A dated record under
`design/` is never edited in place — it is superseded by a later dated record that names
the one it replaces, and the earlier record is left readable so the reasoning survives.

| File | Status | Owns | Not for |
|---|---|---|---|
| [`BYLAWS.md`](BYLAWS.md) | LIVE | Bylaws: roles, stages, profiles, skills, autonomy | — |
| [`COMMANDS.md`](COMMANDS.md) | LIVE | Every command the distribution registers, generated from the registry | — |
| [`CONTRACT.md`](CONTRACT.md) | LIVE | The harness contract | — |
| [`INTENT.md`](INTENT.md) | LIVE | Intent | — |
| [`2026-10-16-capability-layer-audit.md`](design/2026-10-16-capability-layer-audit.md) | LIVE | 2026-10-16: the capability-layer audit — a work list for this repository | — |
| [`2026-10-17-kb-architecture-audit.md`](design/2026-10-17-kb-architecture-audit.md) | LIVE | 2026-10-17: the knowledge-base audit — a work list for this repository | — |
| [`2026-10-19-holon-knowledge-layer.md`](plans/2026-10-19-holon-knowledge-layer.md) | LIVE | The knowledge layer: what a knowledge base is, and phases K1 to K4 | — |
| [`install-script-patterns-2026-10-17.md`](install-script-patterns-2026-10-17.md) | LIVE | Install Script Patterns — Compressed Findings | — |
| [`RECORDS.md`](RECORDS.md) | LIVE | Records — decisions (AD) and regressions (RG) — Generated zones below are written by genome rec emit; never hand-edit them. Topic → governing record… | — |
| [`ASKS.md`](kb/ASKS.md) | LIVE | Operator questions and rulings — genome q add "<question>" · genome q rule Q1 "<ruling>" --applied <owner path>. A ruling is done onl… | — |
