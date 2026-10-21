# 2026-10-21 — Red-team audit: agent-facing reachability and the two-CLI seam

**Status:** record of findings. §1 is measured on this checkout (`main` bb799aa); §2 is the
implementation plan, and the work items wait on the Chair's ratification, not on this record.

**Question it answers:** whenever any agent in this agentic-OS works on anything, can it reach
everything, from anywhere, in the fewest tokens and tool calls? The Chair's instinct was that the
whole holon agentic-OS should itself be a `/project-genome` KB and be folded into one CLI so the
fragmentation disappears. This audit measures whether that instinct is right, and what the minimal
change is that closes the gap it names.

**Method.** Trace, do not read claims: open the cited line, follow one value through every
consumer, and prove each finding by performing the blocked action on a scratch instance. Every
finding below is labelled **confirmed** (reproduced) or **plausible** (the mechanism is clear and
the seam is real, but the triggering configuration was not exercised). The instrument was
`holon/mcp.py`'s `AGENT_COMMANDS` (33 entries) read against `holon/cli.py`'s `REGISTRY` (119
entries), plus a scratch instance with one department, one employee and one ready stage.

---

## 1. Findings

### F1. **Confirmed — 23 commands an agent may lawfully run are unreachable through the MCP surface**

`AGENT_COMMANDS` is the entire agent-facing read/write list for a harness that mounts Holon as an
MCP server. `REGISTRY` has 119 commands; 35 are exposed (33 in `AGENT_COMMANDS`, plus the two
MCP-only tools `holon_whoami` and `holon_submit`). The 84 that remain break down as 53
`chair`, 7 `kernel`, 1 `intake` — which is correct, those are the Chair's or the kernel's — and
**23 declared `actor = "any"`**, i.e. the registry's own statement that no approval is needed to
run them. The actor class is the law; the MCP list is the enforcement of it. These 23 are the
law saying one thing and the interface saying another:

`status` · `emp list` · `emp show` · `hr review` · `dept list` · `dept profiles` · `task list` ·
`plan queue` · `objective show` · `schedule list` · `charter authority` · `charter autonomy` ·
`contract schemas` · `skill check` · `audit samples` · `drivers` · `scope` · `route` · `lint` ·
`doctor` · `chair-channels` · `help` · `mcp`

An MCP-capable employee therefore cannot list the work it is supposed to claim, cannot list its
colleagues, and cannot read who may approve which grade. Note `holon help`'s own description:
"this command registry (--json: the manifest agents read)". The command that exists so agents
read the manifest is itself not in the manifest of commands agents may call.

### F2. **Confirmed — `claim` is exposed with no way to discover what to claim**

`claim` is in `AGENT_COMMANDS`. Its only id source is `task list --stages` (not exposed) or
`task tree <task_id>` (exposed, but its required positional is a task id that only `task list`
produces). Reproduced on a scratch instance with one ready `shape` stage: `task tree` fails with
"the following arguments are required: task_id", and `claim` reports "nothing to claim" because
the agent has no way to name the stage. `run show <run id>` is the same shape: it takes an id the
agent cannot obtain. **Two of the 35 exposed tools are unusable as shipped.** This is the
one-call-too-many problem in its purest form: not a missing capability, but a discoverability
hole that makes a present capability inert.

### F3. **Confirmed — `holon recall` cannot reach the distribution's own law**

`recall` is exposed and joins memory, episodes, skills and playbooks. Querying it for
"trust boundary" on a seeded instance returns skill fragments and **zero lines from
`INTENT.md`, `BYLAWS.md`, `CONTRACT.md` or `AGENTS.md`** — the four documents that state the laws
the agent is audited against. Meanwhile `scripts/genome ask "why does the trust boundary exist"`
ran in the same repo returns `docs/BYLAWS.md:336-360 § 11. Isolation` with a relevance score.
**The two knowledge stores are complete and disjoint, and no single query joins them.** An agent
that needs the law of a mechanism and the memory of a past attempt pays two calls against two
CLIs, and must already know that both exist.

### F4. **Confirmed — holon is already a genome KB, so the Chair's precondition is met; the gap is the seam, not the bootstrap**

`genome.toml` is present and `python3 scripts/genome lint` passes (0 errors, 1 warning: W009,
`docs/ARCHITECTURE.md` — the doc this session added — is not linked from README or any docs
page). `scripts/genome` and `holon/vendor/genome.py` are byte-identical, which the pin and its
guard enforce. So the distribution *is* a knowledge genome with roles, records, register,
decisions and a ledger. **Initializing it as a KB is not the work. The work is that the kernel
does not treat that KB as one of its reachable knowledge bases.**

### F5. **Plausible — the KB layer is department-scoped only, so the distribution's own KB can never bind**

`holon/capabilities/kb.py` binds a knowledge base to exactly one department
(`_may_see`: the Chair, or an employee of that department, or nothing), and `wake_for_dept` runs
`genome wake` for the KBs of one department. A distribution-scope KB — the repo you are reading
this from — has no department, so under the current binding it is either unreadable or readable
by everyone, and `_may_see` has no branch that admits it. This is the structural reason F3 cannot
be fixed by "just seeding another KB".

### F6. **Confirmed — the bundle never carries the orientation the KB already computed**

`compile_context` assembles the brief in ~13 sections over a 24,000-char budget and includes the
department's KB `wake` (capped at 1500 chars per KB, `kb.WAKE_CAP`). It does not include the
one thing `genome wake` computes for free: the register's open items and the repo's own health
("9 open ... doctor 0 finding(s), lint 0 error(s), 1 warning(s)"). For an employee working in the
distribution repo, that line is the highest-value context in the system and it is absent.

### F7. **Confirmed — the repo's own doc gate and the kernel's lint are two gates with two rule sets**

`scripts/gate.sh` (genome `close`) and `holon lint` both run the tests and the strict lint, and
disagree in at least one measurable way: `genome lint` reports W009 for an unlinked page, while
`holon lint --strict` requires zero warnings and is silent about page linkage. Two commands, two
rubrics, one repository. The AGENTS.md ledger already records the analogous failure for the
vendored CLI being re-pinned out of step with `scripts/genome`; this is that seam's doc-side twin.

### F8. **Plausible — the CLI/MCP split is a maintenance tax that grows with the command surface**

`_tool_name` derives `holon_<path>` mechanically, and `tools()` builds schemas from the registry,
so the derivation is sound. But `AGENT_COMMANDS` is a hand-maintained tuple of 33 strings, and
`call()` resolves a tool by searching `REGISTRY` for a name that is *also* in that tuple. A new
command that should be agent-visible requires touching the tuple; nothing detects the omission
(which is precisely how F1 and F2 arose). The cost is not today's 23 entries; it is that the next
40 commands repeat it silently.

---

## 2. Implementation plan — three phases, smallest first

The guiding constraint: **the kernel keeps one writer per database and no harness named under
`holon/`, and the agent-facing surface must remain a closed allowlist, not the whole registry.**
Opening everything would break law 3 and law 8's spirit and hand employees the Chair's commands.
The fix is a *derived* allowlist from the actor class, not a bigger hand-written tuple.

### Phase A — make the allowlist derived and complete (closes F1, F2, F8)

- Derive `AGENT_COMMANDS` from the registry instead of hand-maintaining it: a command is
  agent-visible when its `actor` is `any` or `employee` **and** it is not in a small explicit
  `AGENT_BLOCKLIST` (for `submit`-shaped writes the kernel mediates, and the recursive `mcp`).
  The Chair's 53 commands stay closed by construction, and a new `any` command is agent-visible
  the day it is registered — with its help text and its schemas, from the same source the CLI
  uses.
- Add the two dozen read commands F1 names, and add the lister each exposed tool needs
  (`task list`, `emp list`, `plan queue`) so no exposed tool takes an id it cannot obtain.
- Guard it: a test that asserts every `AGENT_COMMANDS` entry's actor class is `any` or `employee`
  (so the allowlist can never drift into Chair commands), and that every exposed tool's
  *required positional* is discoverable through another exposed command. F2 becomes a permanent
  failure, not a one-time repair.

### Phase B — bind the distribution's KB and join the two knowledge stores (closes F3, F4, F5, F6)

- Seed a **distribution-scope** KB from a `holon` blueprint, and give `_may_see` the branch F5
  lacks: an `org:` scope every employee may read and none may write. This is the only change that
  lets `holon recall` reach `INTENT.md` and `BYLAWS.md` without handing employees the pen.
- Route genome's retrieval primitive into the kernel's: `holon recall` gains a section that runs
  `genome ask` over the distribution KB and renders its `path:lines` hits (the same
  `AGENTS.md:76-82 § ...` form genome already produces) as a source-weighted section, beside
  memory's own sources. One query joins law, memory, skills and playbooks.
- Carry the register line into the brief: `compile_context` adds the org-scope KB's `wake`
  register and health, in the same 1500-char cap the department KBs already use.
- Fold the exit into one command: `holon kb run <partial-slug> <verb>` resolves a unique prefix of
  a KB slug, so `holon kb run org ask "..."` is one call, not a slug lookup plus a call. The
  Chair's "minimize fragmentation" is this line.

### Phase C — one gate and one doc (closes F7, and F1's doc half)

- Make `holon lint` run genome's checks as a pack (or `scripts/gate.sh` delegate to `holon lint`),
  so the two rule sets cannot disagree: W009 becomes a warning the strict lint reports, and the
  near-miss history in the ledger (`scripts/genome` re-pinned while `holon/vendor/genome.py` stayed
  behind) stops being possible in the doc direction.
- Link `docs/ARCHITECTURE.md` from README or a docs page to clear W009 (the one warning this
  session's own docs introduced). `docs/COMMANDS.md` is already listed in `docs/PAGE-INDEX.md`;
  the F1 fix is to make its "every command, by family" claim agree with the MCP allowlist, by
  generating an agent-reachable marker into the reference from the same derivation Phase A uses.

### What this plan deliberately does not do

- It does not expose the Chair's 53 commands. The actor class is the law and it stays the
  enforcement.
- It does not put a second writer on any database. The genome KB stays the genome CLI's writer;
  the kernel only reads it, through the existing per-KB lock and the existing read path.
- It does not bootstrap a new KB structure. `genome.toml` already exists and already lints — F4.
- It does not name a harness. Law 2 applies to every phase.

---

## 3. Evidence

| # | Claim | Command / file | Observed |
|---|---|---|---|
| F1 | 23 `any` commands unreachable | `python3 -c "from holon.mcp import AGENT_COMMANDS; from holon.cli import REGISTRY ..."` | 119 registered, 35 exposed, 23 `any` unreachable |
| F2 | `claim` cannot find a stage | scratch instance, one ready `shape` stage | `task tree` → "requires task_id"; `claim` → "nothing to claim" |
| F3 | `recall` reaches no law docs | `holon --json recall "trust boundary"` | 6 skill hits, 0 of INTENT/BYLAWS/CONTRACT/AGENTS |
| F3 | genome reaches the law | `scripts/genome ask "why does the trust boundary exist"` | `docs/BYLAWS.md:336-360 § 11. Isolation` |
| F4 | the repo is already a genome KB | `python3 scripts/genome lint` | 0 errors, 1 warning (W009, ARCHITECTURE.md unlinked) |
| F4 | the two CLIs are one build | `md5sum scripts/genome holon/vendor/genome.py` | identical |
| F5 | `_may_see` has no org scope | `holon/capabilities/kb.py:93-99` | chair, or employee of the KB's dept, else refused |
| F6 | the brief has no register line | `holon/capabilities/context.py` `compile_context`, §6 `_kb` | dept KBs only, `WAKE_CAP` 1500 |
| F8 | the allowlist is hand-maintained | `holon/mcp.py:27-31`, `:225` | 33-string tuple; resolution by REGISTRY search |
| F7 | two gates disagree | `scripts/genome lint` vs `holon lint --strict` | W009 vs 0 warnings |
