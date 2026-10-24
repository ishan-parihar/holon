---
status: shipped
date: 2026-10-19
description: "The knowledge-layer plan: a knowledge base (KB) as one git repository per department, driven by the vendored project-genome CLI, with a seat policy, a draft-then-promote catalog, and the KB's wake text in the context bundle. Phases K1 to K4, all shipped."
tags: [plan, knowledge-base, genome, capability-layer]
---

# 2026-10-19: the knowledge layer — K1 to K4

Status: **shipped** (this is the plan the kernel cites, written after the fact to match what
landed). The mechanism lives in `holon/capabilities/kb.py`; the surface is `holon kb …`
(`holon/cli.py`); the guard is `tests/test_kb.py`.

## 0. What a knowledge base is

A **knowledge base** is one git repository under `$HOLON_HOME/kb/<slug>/`, driven by the vendored
`project-genome` CLI (`holon/vendor/genome.py`, pinned by checksum). It is bound to exactly one
department.

Three properties follow, and they are the whole design:

1. **The kernel is the KB's only writer.** Every write is one verb, one commit, by the seat that
   made it. Nothing edits a KB's files behind the kernel's back.
2. **A KB is a department's, not a person's.** The seat policy (`_may_see`) admits the Chair and
   the employees of the bound department, and nobody else — not even an employee of another
   department.
3. **The KB's text reaches the work.** `genome wake` of each KB bound to a department is a
   section of the context bundle, budgeted like any other.

The KB is *not* a second memory store. It holds records that have a lifecycle and a history
(commits, links, versions); `memory.db` holds what the organization learned. The two meet at
`kb seed`, which records the KB in the department's memory scope.

## 1. The phases

| Phase | What it is | Surface | Gate |
|---|---|---|---|
| **K1** | seed and read: a KB is created from a blueprint, bound to a department, registered; `kb list` shows what you may see | `holon kb seed`, `holon kb list` | a fresh instance seeds a KB, lints it, and the registry names its department |
| **K2** | verbs under the seat policy: one genome verb per call, read or write, each write committed by its seat | `holon kb run SLUG VERB [ARGS…]` | a write commits; a verb outside the list is refused; `--human` is refused |
| **K3** | drafts and the catalog: an employee drafts a blueprint from a base, the Chair promotes it into the catalog | `holon kb blueprint draft` / `check` / `promote` | a draft that fails `genome blueprint check` is not promotable; promotion is the Chair's |
| **K4** | context: the KB's `wake` text joins the bundle | `kb.wake_for_dept` | a run in the bound department's brief carries the KB's wake text, capped |

**The verbs.** Read: `find`, `view`, `ask`, `wake`, `show`, `lint`, `feed` (the `agents` module's
append-only mentions channel — a read of a file its own `post` verb appends to; the write half stays
refused until the Chair rules on provenance). Write: `new`, `move`, `set`, `link`. A verb outside
these lists is refused — the kernel knows which genome verbs are safe to
expose to a seat, and it does not pass the rest through.

## 2. The rules that are not negotiable

- **One writer.** The kernel is the KB's only writer. A human-only move (`genome move --human`) is
  made by the operator in the repository, never through `holon` — and the kernel refuses the
  `--human` flag (and any unambiguous prefix of it, which genome's argparse would accept).
- **One commit per write.** A write verb commits with the seat's name, so the history says who
  wrote what.
- **The seat policy is a fence, not a hint.** A KB bound to `dept:eng` is invisible to an
  employee of `dept:ops` — `kb list` omits it, and `kb run` on it is refused.
- **The lock is per KB.** Each verb runs under a `.<slug>.lock` flock, so two agents writing the
  same KB at once cannot interleave.

## 3. The trust boundary

The KB tree is a **read** for the agent user, never a grant: the kernel is the KB's only writer
(one verb, one commit, one seat), so an agent may read and diff the KB it is bound to and may not
write it (`docs/BYLAWS.md` §11, the isolation row for `$HOLON_HOME/kb`).

## 4. What is deliberately not here

- **No human writes through the kernel.** `--human` is refused; the operator works in the
  repository.
- **No KB per employee.** A KB is a department's. An employee's own material is `emp:`-scoped
  memory, not a repository.
- **No index.** The KB is a git repository with the genome CLI's own search; the kernel holds no
  derived index of it, so there is nothing to drift.

## 5. See also

- `docs/design/2026-10-17-kb-architecture-audit.md` — the audit of the kernel as a knowledge base
  (the defects and the rulings that preceded this).
- `holon/capabilities/kb.py` — the mechanism; `tests/test_kb.py` — its guard.
- `docs/BYLAWS.md` — the organization this belongs to.
