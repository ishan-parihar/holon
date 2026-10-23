# The isolation red-team audit — measured, and the gap in its operationalization

Status: 2026-10-23. Live instance `/home/ishanp/holon` (read-only). Repo public `9248e3b`.
Every number below is from a command run against the live instance or a fresh one; nothing here
is asserted from reading the source.

The Chair's question: *"Test the isolation system to ensure that it is functional. Last time we
checked it wasn't operational and efficacious in agentic workflows. Investigate the gaps in its
operationalization, and provide a red-team audit."*

Two questions, in order. Is the mechanism sound? Is the system on? The answers differ.

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

Three defects found, each with a guard, each mutation-verified.

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
instance, `/home/ishanp/.jcode/builds/versions/0.99.1` now appears, so the payload and the
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

---

## 2. What is on the live instance: nothing

| Layer | State |
|---|---|
| `[isolation]` config | **no section at all** in `holon.toml` |
| the agent user | **does not exist** (`id holon-agent`: no such user) |
| the runner unit | **not installed** (`/etc/systemd/system`: none) |
| the broker unit | **not installed** (`systemctl --user list-unit-files`: tick and the mirror only) |

`2026-10-22-live-activation.md` §4 measured this three days earlier and named the one blocker:
root must create the agent user, one command away. It is still not created, and `agent_user` was
subsequently reset to empty — which downgrades the finding from an `isolation.runner` *error* (the
kernel refusing to let agents run as its own user once an agent user is promised) to the
`isolation.off` warning of §1c.

Non-interactive `sudo` is not available in this session, so the root step is the Chair's to take.
The command is written and waiting (§1d fixed the step that made it unwritable from a cold start):

```sh
holon isolation plan --write /tmp/holon-isolation.sh   # review it: it now runs from this state
sudo sh /tmp/holon-isolation.sh                        # root: the agent user and its runner service
# then write the [isolation] block the plan printed into holon.toml, and:
holon isolation apply --force                           # 73 setfacl commands (the ACLs)
systemctl --user enable --now holon-broker              # install.py writes this unit
holon doctor                                            # the isolation checks, probed as holon-agent
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

The mechanism is sound — three of its seams I attacked incorrectly, and the two defects I found
inside it are fixed with guards. The **system is not on**: the boundary is absent from the live
instance by one root command, and until this change its absence was invisible to the only thing
that reports unattended. Both halves are now fixed; the root command is the Chair's.
