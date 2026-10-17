# TODO — the register

One row per open work item; rows update in place, verdicts append — never
delete. `scripts/kb status <id> <state> --evidence <path>` is the supervised
transition (terminal states demand evidence; claims are flock-guarded via
`scripts/kb claim-register`).

Seeded 2026-10-17 from `docs/design/2026-10-16-capability-layer-audit.md`'s
work list and its execution-status note. §1's defect fixes carry their guards
in the AGENTS.md regression ledger, not here — this register holds the work
that is still open or only recently closed at the item level.

| id | status | item / verdict | notes |
|---|---|---|---|
| H1 | blocked | C1 surface units (manifests with `category = "surface"`, per-service `effect`/`note`/`args`/`output`/`health`) and the `api_version 4` migration that rides with it | waits on the Chair (audit §2); operant already accepts v4 (`a_v4_kernel_is_read_and_an_unknown_still_refuses`, iter 885) |
| H2 | blocked | R-04 the runtime indexes the registry (`api_pool` view, C1's columns on `api_services`) | after the kernel publishes v4 — i.e. after H1; audit §3 step 3 |
| H3 | blocked | R-13 holarchic config (additive inheritance down the department tree; `owner` departments for units) | waits on the Chair; audit §2 |
| H4 | blocked | R-14 a document knowledge base (markdown-with-frontmatter source, derived `kb.db`, `holon kb` commands, a "Knowledge base" bundle section) | waits on the Chair; audit §2. Note: overlaps this register's own tooling — if adopted, it may take over the in-repo roles `kb.toml` maps, and this file's machinery becomes `native:` |
| H5 | blocked | R-16 the complete system-design skill (references for units/pooling, reach, routines, playbooks; two lint warnings) | waits on the Chair; audit §2 |
| H6 | blocked | N-1 Notion surfaces for the Chair (status page, routine enable, incident ack, capabilities, mirror window) | waits on the Chair; audit §2 |
| H7 | done | audit §1 defect fixes (R-01, R-05, R-06, R-08/R-10, R-09, R-11, R-20) → [2026-10-17] AGENTS.md regression-ledger rows + `docs/design/2026-10-16-capability-layer-audit.md` §4 execution status — verdict: executed and re-audited (red-team pass, commit `bee3bbb`) | every fix traced to its consumer; each guard reverted once to watch it fail |
| H8 | done | §3 steps 1–2: §1 shipped with no view changes; operant accepts api v4 → [2026-10-17] `docs/design/2026-10-16-capability-layer-audit.md` §3 — verdict: kernel stays on v3 until H1 | the read-API trap is fenced |
