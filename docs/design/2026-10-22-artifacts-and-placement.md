# Artifacts and placement: the operating layer the grades never reached

Status: **in execution** (2026-10-22). §1's defects are being fixed now, each with a guard and a
ledger row; §2's changes wait on the Chair. Scrubbed of operator context: every path cited is
either the distribution's own or a variable.

## §0 What was measured

The audit question was the Chair's: as autonomous agents operationalize, what makes work
**fragile** (records that rot), **fragmented** (capabilities scattered where they were born) and
**redundant** (work replanned, scripts duplicated)? Measured on a live instance, 2026-10-21/22:

1. **Artifact pointers rot (confirmed).** Six execute results carry non-empty `artifacts`; two
   resolve to paths that no longer exist (scratchpads from a retired harness era). The permanent
   record names things the organization can no longer reach. `writes` are graded, fenced and
   re-checked; `artifacts` have **no consumer at all** — grep over the kernel finds the field
   defined in `contract.py` and named once in the executor skill ("leave evidence") and nowhere
   else. Existence is the verifier's glance at submit time, once, and never again.
2. **Placement is unfenced (confirmed).** A directory at the instance root that no workdir record
   names, no `[scope]` root covers and no config key references was created and used as five
   tasks' workdir. Every other root-level directory is kernel vocabulary; this one grew because a
   body put it there. The org scope is `roots: []` — an empty reach means every absolute write
   outside the workdir grades G2 "outside the task workdir", which is a *fee*, not a *place to
   put things*, and nothing anywhere says where durable things live.
3. **Pooling has the mechanism and no workflow (confirmed).** The units layer (U1–U3) is exactly
   the pooling mechanism: one manifest declares a capability's services, renders them into every
   pooler's brief, grants read reach, fences its binaries. The live instance holds **one**
   manifest. `venture import` lands scripts wherever the venture kept them, and nothing ever asks
   "should this become a unit?" The pipe from "a script a department runs" to "a declared, pooled,
   health-checked capability" does not exist.
4. **The register drifts (confirmed).** `TODO.md` said H4 "blocked: a document knowledge base,
   waits on the Chair" while K1–K4 had shipped (`kb list` works, a KB exists, the brief carries
   it); H10's Tier-0 claims (operant loader, gateway, unstaffed stages, stale-export count) were
   re-measured false. The register updates when a session remembers to.
5. **Memory's `artifact` kind is unused (confirmed).** `vocab.MEMORY_KINDS` carries `artifact`
   ("a pointer to something outside memory"); the live instance holds exactly one such item, and
   it points at a knowledge base, not at work output. The executor would have to remember,
   unprompted, to record each artifact there.
6. **The contract field has no teeth (confirmed).** `artifacts: STR_LIST` accepts anything —
   prose, typos, relative fragments — and nothing validates it as paths, so a typo lands silently
   and rots undetected (finding 1's root cause).

Mapped: fragility = 1+6, fragmentation = 3, redundancy = 3+4.

## §1 Defects to fix now (each lands with a guard + ledger row)

| # | Defect | Fix | Guard |
|---|---|---|---|
| A1 | `artifacts` entries were never paths — any string passes | `contract.py`: execute's `artifacts` becomes a list of absolute-or-workdir-relative path strings (`//`-free, no URL schemes); validation errors name the offending entry | `test_artifacts_entries_must_be_paths` |
| A2 | nothing pins an artifact anywhere; the record rots in `outputs` | `service.submit` pins each execute artifact to memory as `kind=artifact` in the work's scope, provenance `work:<run_id>`, at submit (so it is queryable before verification, like every other consolidation) | `test_submitted_artifacts_are_pinned_to_memory` |
| A3 | a dangling artifact pointer is invisible until someone needs it | `doctor`: `artifacts.dangling` re-resolves every execute output's artifacts; a missing path is a warning naming the work, the path and the fix | `test_doctor_reports_a_dangling_artifact_pointer` |
| A4 | an undeclared directory can grow at the instance root | `doctor`: `scope.stray` lists directories at `$HOLON_HOME` root that are neither kernel vocabulary nor a declared workdir/scope root | `test_doctor_reports_a_stray_root_directory` |
| A5 | no law says where durable things live, so each agent improvises | the execute brief's Result contract section states the placement law (workdir or a scope root; every durable thing named in `artifacts`); `holon-executor` skill updated to match | brief render test + skill docs check |
| A6 | grading never looked at `artifacts`, only `writes` — an artifact outside reach graded as nothing | `grade_plan`: an absolute artifact path outside workdir+reach floors G2 with its reason (same walk as writes, minus the pooled-unit rule: artifacts are read pointers, not writes) | `test_an_artifact_outside_reach_raises_the_floor` |
| A7 | `gc` could collect a run directory a pinned artifact still points at | `gc.plan` keeps the run dir of any work whose memory holds an `artifact` item pointing into it | `test_gc_keeps_a_run_dir_a_pinned_artifact_still_points_into` |
| A8 | the register's H4 row said "blocked" for a shipped mechanism | `TODO.md` corrected in place (the register's own rule: rows update in place) | — (the row itself is the fix; the hygiene check below guards the class) |
| A9 | register drift is invisible until someone re-measures by hand | new lint pack `register`: a row whose status is `blocked` while the named mechanism's guard file exists (H4's shape) is a warning naming the row and the evidence | `test_a_blocked_register_row_for_a_shipped_mechanism_is_drift` |
| A10 | a repeated script never becomes a declared capability | `holon bylaws draft` (employee) proposes a unit manifest from observed usage: slug, charter line, one service per observed command; it lands in a drafts area, changes no state, and `holon bylaws promote` (chair) moves it into `bylaws/units/` after the existing `validate` runs | `test_a_draft_unit_lands_only_when_the_chair_promotes_it` |

A2's provenance choice: `work:<run_id>` (the same prefix `consolidate` uses), so `memory show`
renders the run as the edge's source and `recall` answers "where is X" in one call.

## §2 Changes that wait on the Chair

| # | Change | Why it waits |
|---|---|---|
| B1 | a `[units]` config key for draft proposals' defaults (pool_of, category) | new config keys are the Chair's; the draft command works without it (defaults: `pool_of = []`, employee names them) |
| B2 | `doctor` check: a department running the same ad-hoc script path across N works suggests a draft | needs a threshold the Chair sets; the draft command (A10) is the manual half |
| B3 | ventures import proposing unit drafts for its scripts | the ventures program is Chair-gated (law 10); A10's draft path is the kernel's half |
| B4 | a recurring routine that re-measures Tier-0 claims (H10's class) and files corrections | the heartbeat's interval and its existence on a given machine are the operator's |

## §3 Verification

- full suite + `holon lint --strict` after each phase; guards reverted once to watch them fail.
- the live instance's own strays: doctor's new checks name what they find; the Chair decides
  whether `notes-digest` moves into a scope root or is retired.

## §4 Execution status

- [x] 2026-10-22 §1 A1–A10 landed (guards green, ledger rows added); see AGENTS.md.
