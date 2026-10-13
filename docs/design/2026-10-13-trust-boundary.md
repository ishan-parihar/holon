# The trust boundary: an agent user, a runner and a broker (2026-10-13)

Status: built (this commit); the one-time root setup on an instance waits for the Chair. Closes G1 of
`2026-10-13-paperclip-contrast.md`; the mechanism is documented in `docs/GENOME.md` §11.

## 1. The problem

Every guarantee Holon makes (run tokens, separation of duties, grade floors, approvals) is checked
inside the `holon` process. Agents ran as the kernel's Unix user, so an agent with a shell could
read the Chair key (`~/.config/holon/keys/*.chair`, 0600 but the same owner), the Notion and model
tokens, or write `work.db` with `sqlite3`. On the Chair's workstation that user is also in the
`docker` group, which is root-equivalent. Only ZeroClaw's adapter denied `~/.config`; the jcode and
Hermes adapters gave agents an unconfined shell.

The Chair chose (2026-10-13): separate Unix users and a kernel broker, over per-harness filesystem
confinement, so the boundary holds for every harness without trusting any of them.

## 2. Decisions

| Decision | Why |
|---|---|
| A **runner** service runs as the agent user and starts processes for the kernel | a non-root kernel cannot switch users; sudo's signal and pty behaviour would make stopping a run unreliable; a runner of the same uid as the harness can always stop it |
| A **broker** service runs as the kernel user and serves the CLI to agents | the databases and keys stay readable only by the kernel; agents keep the same `holon` commands and MCP tools |
| The client switch lives in `cli.main` (`HOLON_BROKER` set ⇒ forward argv) | every way an agent reaches the kernel (shell, skills, MCP tools) goes through one function |
| The broker authenticates only run tokens and employee keys (`Auth.agent_principal`), allows only agent commands, strips `--home`/`--token`, and never reads a path the client names (file arguments travel in the request) | the broker runs where the Chair key is readable: it must not become a confused deputy |
| Skill names are validated (`NAME_RE`) | `skill show ../../../.config/holon/keys …` read any file through a name that was never checked; harmless as one user, a key leak through a broker |
| Abstract Unix sockets plus an `SO_PEERCRED` uid check | nothing on disk to squat or chmod; only the two users may connect |
| Machine checks and script routines also run through the runner | they execute code in agent-written trees; as the kernel's user that would be a privilege escalation |
| Run directories: sticky, kernel files (including `driver.log`) created before the agent starts, the agent granted rwx by ACL; `result.json` read with `O_NOFOLLOW`, regular file, ≤ 8 MB | an agent can hand in a result but cannot plant a symlink where the kernel reads or writes |
| ACLs allow traversal only along chains to what the agent needs, and deny every other entry on those chains, now and in future (default ACL) | the Chair's home is 0700 but nearly everything inside it is 0755/0644: a plain traverse grant on it would expose `~/.config` and the rest |
| `holon doctor` probes the boundary from the agent's side, through the runner | the boundary is a tested invariant on every heartbeat, not a setup step remembered once |

## 3. What is left to the Chair

The kernel cannot create users or system services. On an instance:

1. `holon isolation plan --write /tmp/holon-isolation.sh`, read it, `sudo sh /tmp/holon-isolation.sh`
   (creates `holon-agent` and `holon-runner-<instance>.service`).
2. Add `[isolation] agent_user = "holon-agent"` and any `grants` (workdir trees outside `[scope]`).
3. `holon isolation apply`, `python3 install.py --systemd` (writes and starts `holon-broker`).
4. Re-run the harness installers so their homes are granted, then `holon doctor` and
   `holon adapter test <driver>`.

## 4. Limits

- Agents share one Unix user: one agent can read another's run directory and its run token.
  Per-department users would close this; not built.
- The model key a harness needs is readable by the agent user by design.
- A harness whose binary lives in a tree the plan does not see (a virtualenv beside its entry point)
  needs `[isolation] reads`; doctor's `isolation.reach` names what the agent cannot read.
- Tested end to end as one user (runner and broker over real sockets, a push run that self-submits
  through the broker, checks through the runner, doctor's probe); the uid switch itself is
  exercised only by the root-installed service.
