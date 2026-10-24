# The isolation red-team audit — measured, and the gap in its operationalization

Status: 2026-10-23. Live instance `$HOLON_HOME` (read-only). Repo public `793f60e`.
Every number below is from a command run against the live instance or a fresh one; nothing here
is asserted from reading the source.

The Chair's question: *"Test the isolation system to ensure that it is functional. Last time we
checked it wasn't operational and efficacious in agentic workflows. Investigate the gaps in its
operationalization, and provide a red-team audit."*

Two questions, in order. Is the mechanism sound? Is the system on? The answers differ.

Each requirement below is mapped to the check that covers it and the result observed on a real
interface — `docs/design/2026-10-23-isolation-audit-requirement-map.md`. A test count establishes
neither.

---

## 1. The mechanism, probed as an adversary

`tests/test_isolation.py` proves the boundary the way its author would. `tests/test_isolation_
redteam.py` attacks it the way a body would. What held:

| Seam | Probed | Result |
|---|---|---|
| the broker's first gate | a peer uid outside `allowed_uids` | refused, before any command runs |
| the broker's authority | no credential, chair-only commands | refused: "this is not the Chair" |
| the broker's file access | `_read` on a path an agent names | opens only what the client sent |
| the run directory | a symlink planted at `result.json` | refused (O_NOFOLLOW), not followed |
| the run directory's ACL | the agent's grant | exactly `rwx` on the dir, `r--` on the kernel's files, `other::---`, sticky |

Five defects found, each with a guard, each mutation-verified.

### 1a. The isolated harness was the *less* filtered of the two branches

`drivers.run` promises the harness "only the run's environment (never the kernel's)". The runner
implemented that as `env={**os.environ, **req.get("env", {})}` — the caller's dict merged **over
the runner's whole environment**. Measured: a name the runner inherited reached the spawned
process, and the caller could override any name (`HOLON_HOME`, `PYTHONPATH`, anything).

The contrast that settles it is inside `drivers.run` itself. The non-isolated branch writes
`clean = {k: v for k, v in os.environ.items() if k not in ("HOLON_CHAIR_KEY", "HOLON_KEY")}` and
merges that under the run's env. So the *un-isolated* path filtered two credential names and the
isolated one reintroduced the entire environment one layer down.

Fixed: the spawn gets exactly the environment its caller asked for. An agent user whose reach is
fenced by ACLs must not be handed back through the environment what the ACLs deny it. The unit's
own vector is narrower than the guard implies — the runner is a **system** unit, and
`systemctl show-environment` carries no credential on this host while the *user* manager does
carry one (`POSTIZ_API_KEY`) — so the guard defends the invariant rather than a measured leak.

### 1b. A harness's reach was one file, and a harness is a tree

`reach()` granted the harness binary as the one path `shutil.which` returns. Measured on the live
instance: the harness is a 505-byte launcher beside a 171 MB payload, and the launcher's own text
is `LD_LIBRARY_PATH="$self_dir"` then `exec "$self_dir/jcode-linux-x86_64.bin"`. The launcher was
in the read reach; the payload was not, and neither was the directory holding them.

So the agent user could not start the harness the instance dispatches. A boundary that closes
perfectly and denies the department its own tooling is not a trust boundary — it is a department
that cannot work. Fixed: the harness's own directory is in the read reach. Re-measured on the live
instance, `~/.jcode/builds/versions/0.99.1` now appears, so the payload and the
`LD_LIBRARY_PATH` target are granted.

### 1c. An absent boundary was a warning, so it reached nobody

`holon doctor` on the live instance: `WARN isolation.off · 0 errors, 1 warnings`. The heartbeat's
doctor phase (`service.py`) opens an incident only for `severity == "error"`. So the total failure
of the trust boundary — agents running as the kernel's own user, able to read the Chair key and
write `work.db` — produced no incident, no digest line, and no Chair notification. It is the exact
defect the ledger records as fixed for every *other* check ("doctor ran only when a person ran
it"), surviving in the one check that decides whether the kernel's own databases are writable by
the bodies it dispatches.

The kernel's own policy settled the severity: a promised-but-runnerless boundary is already an
`error` (`isolation.runner`, measured 2026-10-22). An absent boundary is the same exposure a step
wider. It is an error now, and `_isolation()` no longer returns early on an unconfigured
installation — which measured as reporting **nothing** about the boundary at all, so a fresh cold
start reached its first dispatch with no signal either way.

### 1d. The activation plan was unreachable from a cold start

`holon isolation plan` is step 1 of BYLAWS §11's activation sequence: `plan` → `sudo sh` →
`apply --force` → broker → doctor. Measured 2026-10-23 on the live instance, it refuses with
"set [isolation] agent_user first" — so the plan that tells you which user to create could not be
produced until you had already named one, by hand, in holon.toml. **The documented activation path
is broken at its own first step**, and this is the gap that kept the boundary off: the operator
reached step 2 in 2026-10-22, the ACLs failed because the user did not exist, and the user could
not be created without a plan that would not run.

Fixed: `setup_plan` proposes `holon-agent-<instance>` when no user is named, the root script names
the same user it creates, and the command prints the exact `[isolation]` block to write into
holon.toml. Re-measured on the live instance, the plan now runs and produces itself:

```
1. as root, once: sudo sh /tmp/holon-isolation.sh
1.5. write into holon.toml:
     [isolation]
     agent_user = "holon-agent-01m4sf0wr27mgs9e1327aqxp1z"
     runner = "@holon-runner-01m4sf0wr27mgs9e1327aqxp1z"
   as you: holon isolation apply --force   (73 setfacl commands)
```

Nothing downstream trusts the proposal: `apply`, the broker's allowed uids and doctor's probe all
read the config, so a user that exists but is not named changes nothing, and a name that is written
but whose user does not exist is still the `isolation.runner` error the kernel raised in 2026-10-22.

### 1e. Then the fixed output named a step that cannot work

Making the plan reachable was not enough — the sequence it printed had to be walkable. Measured:
step 3 said `systemctl --user enable --now holon-broker (install.py writes the unit)`, and
`install.py` writes that unit **only when `agent_user` is already in `holon.toml`** (`install.py`
line 222: `["holon-broker.service"] if isolated else []`). On a cold start the config is not yet
written, so the unit does not exist and the told command fails — the third instance in this
ledger of a remediation naming a command that cannot work, and this one was mine.

Fixed: the sequence now names `python3 install.py --systemd` at step 3, *after* the config write
that makes it write the broker unit. Proved end-to-end on a fresh instance, not by reading the
flag: with `agent_user` set, `install.py --systemd --no-start` writes
`~/.config/systemd/user/holon-broker.service`. A guard walks every line the plan prints and fails
on a line that names `holon-broker` and `systemctl` without naming `install.py`.

Two defects in the same command found by re-reading its own output rather than trusting the edit:
the step-4 line lost its `f` prefix (printing the literal `{plan['agent_user']}`), and the
no-`--write` path was interpolating a `{w}` placeholder by a post-hoc string replace. Both are gone;
both paths now render cleanly, which is the whole point of printing a sequence a person will type.

---

### 1f. Validating the audit against the applied result, not the printed plan

The audit's own documents were the last thing I checked, and the check was against what the plan
*prints* rather than what `setfacl` *leaves on disk*. That is the wrong seam for a security promise,
and it hid a real one.

`setup_plan`'s docstring claimed every entry in the kernel user's home that is not in the reach
"carries an explicit deny for the agent user". Measured on a real replica of the harness layout,
applying **only the plan's own commands, in its own order**: the payload the launcher execs is named
with `-m u:<agent>:---`, and the directory holding it is granted `rX` afterwards. POSIX ACLs hold
one entry per user, and the last `setfacl` for that user wins, so the payload ends
`user:<agent>:r--` — readable, with the named deny inert.

The outcome is correct. The document was false, and that is the dangerous direction: a reader of the
plan would conclude the payload is denied, and an emission-order change would have made it
unreadable with nothing to say so. Nothing re-read the applied ACLs, so the last mode each path
received was decided by order and unverifiable — 688 directives, no reader.

Two guards hold the outcome now, both mutation-verified rather than merely green:

| Guard | Mutation that fails it | What that proves |
|---|---|---|
| `test_the_applied_acl_matches_what_the_reach_classifies` | granting read targets `rwX` | the last mode for each path must agree with `reach()`'s own classification, so a write grant can never reach a read-only path |
| `test_the_plan_applied_to_a_real_tree_grants_the_harness_it_dispatches` | dropping the recursive `rX` on the version directory | the plan's own commands applied to a real tree must leave launcher and payload readable; without it the payload ends with no agent entry at all, and the harness cannot run |

Measured on the live instance, the classification these agree with: `pioneer-qf` ends `rwX` as a
write, the harness directory and `.local/bin` end `rX` as reads. The docstring now states the
promise with its actual limit and names both guards.

### 1g. Validating the requirement map found three defects, and the third was mine in the direction that matters most

The auto-validation of this audit re-ran every row of
`2026-10-23-isolation-audit-requirement-map.md` against its named interface, rather than
re-trusting the measurements. Five guards were confirmed to exist (5 of 5, by name, in the
module and class each row now names) and to pass by exact name, not by module. Two claims did not
survive, and both are catalogued below.

**Defect 1: the printed activation sequence had no step 1.** Measured on the live instance with
`holon isolation plan` (no `--write`): the root script prints raw, and the numbered list then begins
at **`1.5.`** — the full list was `['1.5', '2', '3', '4']`. An operator counting along has no step 1
to start from, and the 46-line shell they must read is the one step with no label. The guard
`test_the_printed_sequence_numbers_every_step_it_prints` holds two rules: the first numbered step
is `1.`, and every integer level printed appears (1.5 is fine; 2.5 with no 2 is not). Fixed by
labelling the script as its own step 1; re-measured, the output is `1., 1.5., 2., 3., 4.`.

**Defect 2: the map named five guards with no module or class.** Running them as
`RedTeam.<name>` — the module's first class, the natural guess — produced four `AttributeError`s,
because three live in `Reach` and one in `Deployment`. A module pass hides that entirely, because a
module pass is indifferent to which class holds the method. A later attempt to write the names out
went wrong in the *other* direction and is recorded in the map's first row: I typed two **phantom**
guard names and dropped two real ones, caught within the minute by checking each name against the
module. A name in a record that a reader cannot run is a defect in both directions.

**Defect 3, and it was mine in the direction that matters most.** The same round re-measured the
deployment's rows and concluded the operator's `HOLON_HOME` sat one directory too deep, because the
plan's `runs:` line printed a doubled path and a probe reported `writes: -`. Both readings had one
cause, and it was not the deployment: the doubled runtime directory and the empty instance behind
`writes: -` were created by **my own `Kernel(...)` construction**, which writes state even when the
probe only reads. Measured: the doubled tree was created at 02:53 on 2026-10-24, by me. The real
instance (`01m4sf0…`, three departments, seven employees, 166 tasks, 1530 events) sits one level up
at the path `HOLON_HOME` already names, so `HOLON_HOME` needs no change — and a fix written from the
wrong reading would have told the Chair to edit a correct config. §2 records the full measurement.

**The hazard that is not the kernel's, and must not be read as such.** The installed tick unit names
a different, scratch instance and the timer writes that one every two minutes while the real
organization waits. Seven concurrent agent sessions are running on this host. This is the
deployment's problem to resolve, and reporting it as a kernel defect would be the conflation AGENTS.md
law 10 exists to prevent; it is recorded here because it changes what "is it deployed?" means for
the Chair's root command — a boundary activated on the wrong instance protects nothing.

### 1h. Two guards whose docstrings described the defect they were written beside

Enumerating every guard in the module to confirm the map's "all five probes held" row found two
whose docstrings asserted the **unfixed** behaviour of defect 1a: the runner "starts a process with
the runner's whole environment plus the caller's dict merged over it", and the caller "can override
any credential". Both stopped being true when 1a was fixed, and both assertions kept passing because
neither tests the claim its docstring makes — each asserts the half that was never in dispute
(the caller's own value reaches the spawn).

That is a distinct failure from a false document: a reader of the guard names concludes a credential
reaches the spawn when it does not. Settled by re-measurement, not by reading the code — with a
real credential in the runner's inherited environment (**not** a marker of my own in the test's
process), the spawn prints `unset`. The two docstrings now state what they assert, point at
`test_the_isolated_harness_gets_only_the_runs_environment` for the other half, and record that the
assertion outliving its description is how a suite looks green after its premise changed.

The general lesson the module now carries: **a guard's name and docstring are a claim, and the
claim is part of what must be validated.** The map's own rows are the same risk, which is why this
validation round ran the names rather than the module.

---

## 2. What is on the live instance: nothing

| Layer | State (re-measured 2026-10-23, 21:15–21:45) |
|---|---|
| `[isolation]` config | **no section at all** — the config the kernel reads is `$HOLON_HOME/holon.toml` and on this deployment that file is **absent**, so the instance runs on defaults and `doctor` separately reports `security.auth` ("no Chair key", open mode) |
| the agent user | **does not exist** (`id holon-agent-<instance>`: no such user) |
| the runner unit | **not installed** (`/etc/systemd/system`: none) |
| the broker unit | **installed against the wrong instance, and not running.** Corrected: the first version of this table said "not installed", and a re-measurement on 2026-10-23 at 21:15 found `~/.config/systemd/user/holon-broker.service` — `disabled`, `inactive`, and written against a **scratch** `HOLON_HOME`, not the instance that carries the work |

**The deployment is split in two**, and the first version of this section got the reason wrong, which is worth recording because I nearly shipped the wrong fix. Measured:

- the instance that carries the work is `01m4sf0…` — three departments, seven employees, 166 tasks, 1530 events — at the path `HOLON_HOME` already names, so **the operator's `HOLON_HOME` is correct and needs no change**;
- the installed tick unit names a **second, scratch** instance (`01m5mn6z…`, one department, four tasks, five events), and the tick timer writes that one every two minutes rather than the one that carries the work.

My first reading was that `HOLON_HOME` sat one directory too deep, because the plan's `runs:` line printed a doubled path. Re-measured, the doubling is an artifact of *my own probe*: the doubled runtime dir was created at 02:53 on 2026-10-24 by the `Kernel(Home.resolve())` I ran against that home — a fresh empty instance, which is also why that same probe reported `writes: -` and one default `executive` department. Both readings were wrong, and both came from measuring a mechanism against a state my own measurement had created. The lesson is the audit's own: re-measure against the state you found, and never against one you just made.

The real hazard, which neither of my readings was: the tick timer keeps writing the scratch instance while the real organization waits. Seven concurrent agent sessions are running on this host. This is the deployment's to resolve and is recorded rather than fixed, because a kernel change here would be the conflation AGENTS.md law 10 exists to prevent.

**One stray directory this audit left behind, named so the Chair can remove it.** The doubled
runtime tree above (`<live-home>/execution/runtime/execution/`) was created by my own read-only
probes, because constructing a `Kernel` against an instance writes state even when the probe only
reads. It holds one empty `work.db` and an empty `runs/` and nothing else; the live organization's
state is untouched, one level up. It is exactly the class `doctor`'s `scope.stray` reports — a
directory at the instance root named by nothing — and it is mine. Removal is the Chair's call, and
it is the smallest thing on this page:

```sh
rm -rf <live-home>/execution/runtime/execution   # only if it is the empty dir measured here
```

What follows is measured on the instance that carries the work, because that is the one a boundary
would have to protect.

`2026-10-22-live-activation.md` §4 measured this three days earlier and named the one blocker:
root must create the agent user, one command away. It is still not created, and `agent_user` was
subsequently reset to empty — which downgrades the finding from an `isolation.runner` *error* (the
kernel refusing to let agents run as its own user once an agent user is promised) to the
`isolation.off` error of §1c.

Non-interactive `sudo` is not available in this session, so the root step is the Chair's to take.
The command is written and waiting (§1d fixed the step that made it unwritable from a cold start,
and §1g fixed the numbering that made step 1 unlabelled):

```sh
holon isolation plan          # prints steps 1 ... 4 for this instance
holon isolation plan --write /tmp/holon-isolation.sh   # write step 1's script to a file
sudo sh /tmp/holon-isolation.sh   # 1. root: the agent user and the runner service
                                  # 1.5. write the printed [isolation] block into holon.toml
holon isolation apply --force     # 2. the ACLs (29 on the live instance; the count is per instance)
python3 install.py --systemd      # 3. writes holon-broker, now that agent_user is set
holon doctor                      # 4. the isolation checks, probed as the agent user
```

The reach that plan would grant was measured and is sound: the department's own roots and the two
harness homes as writes, the distribution, the knowledge base and the harness trees as reads.

---

## 3. A false positive, recorded

My first hypothesis about `_client_files` was wrong and is recorded here so it is not repeated:
the exfil path I suspected runs on the **agent** side (`forward()`), not the kernel side. The
broker's `_read` opens only what the client actually sent, and a path an agent names but cannot
read is not read for it. A guard now holds that.

## 4. What the mechanism cannot close

- the agent user must exist (root, once) — §2
- the runner unit is a **system** unit, so it does not inherit the user manager's environment; the
  broker is a **user** unit and does. The runner sanitizes nothing, and the §1a fix removes the
  last reason that could matter, but a deployment that puts a credential in the system manager's
  environment should say so
- `reach()` grants a driver's `[env]` directory as a write. That is the harness's own contract,
  and it is the widest thing the boundary grants

## 5. Verdict

The mechanism is sound — all five seams I attacked held. The defects found inside it and around it
are fixed with guards, and **three of them were mine**, which is the number this section should
carry rather than two:

1. making the plan reachable was not enough; the sequence it printed had to be walkable, and the
   first version told the Chair to run a command that could not work (a guard now walks every line);
2. validating the audit against the printed plan instead of the applied ACL over-read what the plan
   promised (§1f);
3. and worst, the validation round measured the deployment against a state **its own probe had just
   created**, and concluded the operator's `HOLON_HOME` was one directory too deep. It is correct. A
   fix written from that reading would have told the Chair to edit a config that already works.

The general lesson, which is the audit's own method turned on the auditor: **every claim about code
or state is a hypothesis, including the auditor's, and the measurement must be taken against the
state you found rather than the one your probe made.** It is the same rule the ledger already states
for design records, applied to the record that was validating them.

The **system is not on**: the boundary is absent from the live instance by one root command, and
until this change its absence was invisible to the only thing that reports unattended. Everything
except the root command is fixed; the root command is the Chair's, and the plan that produces it now
runs from the state the instance is actually in — with the instance it must protect identified by
id (`01m4sf0…`, three departments, seven employees, 166 tasks), because the heartbeat on this host is
currently writing a different one.
