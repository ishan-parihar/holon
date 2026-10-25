"""Isolation: agents run as their own Unix user and reach the organization only through the broker.

Without it, an agent runs as the kernel's user, so any agent with a shell can read the Chair key or
write work.db directly, and every check the kernel makes can be walked around. With `[isolation]
agent_user` set (docs/BYLAWS.md §11):

  runner  a small service running AS THE AGENT USER (installed once by root). The kernel sends it a
          command, a working directory and the run's environment; it starts the process, reports its
          output and exit code, and stops it when the kernel says the run is over. Harnesses, machine
          checks and script routines all start here, so nothing an agent wrote runs as the kernel.
  broker  a small service running AS THE KERNEL'S USER (`holon broker`). An agent's `holon` command
          (and `holon mcp`) sends its argv and its run token or employee key here instead of opening
          the databases. The broker authenticates the token, never the Chair key, allows only the
          commands an employee needs, never takes --home from the caller, and never reads a file on the
          caller's behalf: file arguments travel inside the request, read by the client as the agent.

Both listen on Linux abstract Unix sockets (no file to squat or chmod) and check the caller's uid
(SO_PEERCRED). One request per line, JSON both ways.
"""

from __future__ import annotations

import io
import json
import os
import select
import signal
import socket
import struct
import subprocess
import sys
import shutil
import threading
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

MAX_REQUEST = 16 << 20
MAX_FILE = 8 << 20


class IsolationError(RuntimeError):
    pass


# ── sockets ─────────────────────────────────────────────────────────────
def _addr(address: str) -> str:
    """'@name' is a Linux abstract socket; anything else is a filesystem path."""
    return "\0" + address[1:] if address.startswith("@") else address


def peer_uid(conn: socket.socket) -> int:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return struct.unpack("3i", creds)[1]


def _send(conn: socket.socket, obj: dict) -> None:
    conn.sendall(json.dumps(obj).encode() + b"\n")


def _recv(f) -> dict | None:
    line = f.readline(MAX_REQUEST + 1)
    if not line:
        return None
    if len(line) > MAX_REQUEST:
        raise IsolationError("request too large")
    return json.loads(line)


def listen(address: str) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(_addr(address))
    s.listen(16)
    return s


def connect(address: str, timeout: float | None = 30) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(_addr(address))
    except OSError as e:
        s.close()
        raise IsolationError(f"cannot reach {address}: {e}") from None
    return s


# ── the broker: the organization, for agents ─────────────────────────────
def _strip_globals(argv: list[str]) -> list[str]:
    """Drop the caller's --home and --token: the broker names the instance, the token travels apart."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
        elif a in ("--home", "--token"):
            skip = True
        elif not a.startswith(("--home=", "--token=")):
            out.append(a)
    return out


def handle_broker(conn: socket.socket, home: str, allowed_uids: set[int]) -> None:
    from holon import cli
    with conn, conn.makefile("rb") as f:
        if peer_uid(conn) not in allowed_uids:
            _send(conn, {"code": cli.FORBIDDEN, "stdout": "", "stderr": "holon broker: your uid may not call it\n"})
            return
        try:
            req = _recv(f)
        except (ValueError, IsolationError) as e:
            _send(conn, {"code": 2, "stdout": "", "stderr": f"holon broker: bad request: {e}\n"})
            return
        if req is None:
            return
        out, err = io.StringIO(), io.StringIO()
        stdin = sys.stdin
        sys.stdin = io.StringIO(req.get("stdin") or "")
        try:
            with redirect_stdout(out), redirect_stderr(err):
                try:
                    code = cli.main(["--home", home, *_strip_globals(list(req.get("argv") or []))],
                                    broker={"token": req.get("token"), "key": req.get("key"),
                                            "files": dict(req.get("files") or {})})
                except SystemExit as e:  # argparse usage errors and --help
                    code = e.code if isinstance(e.code, int) else 2
        finally:
            sys.stdin = stdin
        _send(conn, {"code": code, "stdout": out.getvalue(), "stderr": err.getvalue()})


def serve_broker(home: str, address: str, allowed_uids: set[int], stop: threading.Event | None = None,
                 server: socket.socket | None = None) -> None:
    """Serve until `stop` is set. ponytail: one request at a time (cli.main swaps sys.stdout); requests take
    milliseconds, so a thread per request only matters if a slow agent command appears."""
    srv = server or listen(address)
    srv.settimeout(0.5)
    with srv:
        while not (stop and stop.is_set()):
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            conn.settimeout(60)
            try:
                handle_broker(conn, home, allowed_uids)
            except OSError:
                pass


def _client_files(argv: list[str]) -> dict[str, str]:
    """The file arguments of a command (@path for text, --file PATH), read here, as the agent."""
    files, total = {}, 0
    for i, a in enumerate(argv):
        path = key = None
        if a.startswith("@") and len(a) > 1:
            path, key = os.path.expanduser(a[1:]), a[1:]
        elif a == "--file" and i + 1 < len(argv):
            path, key = argv[i + 1], argv[i + 1]
        elif a.startswith("--file="):
            path = key = a[len("--file="):]
        if path is None or key is None:
            continue
        try:
            with open(path) as fh:
                files[key] = fh.read(MAX_FILE + 1)
        except OSError:
            continue  # the broker will say it was not sent
        total += len(files[key])
        if len(files[key]) > MAX_FILE or total > MAX_REQUEST // 2:
            raise IsolationError(f"{path} is too large to send")
    return files


def forward(argv: list[str], address: str) -> int:
    """The client: what `holon …` does when HOLON_BROKER is set (an agent under isolation)."""
    token = os.environ.get("HOLON_RUN_TOKEN")
    for i, a in enumerate(argv):
        if a == "--token" and i + 1 < len(argv):
            token = argv[i + 1]
        elif a.startswith("--token="):
            token = a[len("--token="):]
    req = {"argv": argv, "token": token, "key": os.environ.get("HOLON_KEY"), "files": _client_files(argv),
           "stdin": sys.stdin.read() if "-" in argv else ""}
    try:
        with connect(address, timeout=300) as s, s.makefile("rb") as f:
            _send(s, req)
            resp = _recv(f)
    except (IsolationError, OSError, ValueError) as e:
        print(f"holon: the broker is unreachable ({e})", file=sys.stderr)
        return 1
    if resp is None:
        print("holon: the broker closed the connection", file=sys.stderr)
        return 1
    sys.stdout.write(resp.get("stdout") or "")
    sys.stderr.write(resp.get("stderr") or "")
    return int(resp.get("code") or 0)


# ── the runner: processes, started as the agent user ──────────────────────
def _kill(proc: subprocess.Popen, sig: int) -> None:
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass


def handle_runner(conn: socket.socket, allowed_uids: set[int]) -> None:
    with conn, conn.makefile("rb") as f:
        if peer_uid(conn) not in allowed_uids:
            _send(conn, {"error": "your uid may not start processes here"})
            return
        req = _recv(f)
        if req is None:
            return
        # The process gets ONLY the environment its caller asked for. The runner
        # inherits its service's whole environment (its systemd unit sanitizes nothing
        # beyond NoNewPrivileges, and on a host whose user manager carries a credential
        # — measured 2026-10-23, a real API key in `systemctl --user show-environment` —
        # that name arrives inside every agent process), and merging `os.environ` under
        # the caller's dict hands a body with a shell everything the service sees.
        # drivers.run's own contract is "only the run's environment (never the kernel's)";
        # the non-isolated branch filters the Chair and key names explicitly, so the
        # isolated one must not be the less filtered of the two. An agent user whose
        # reach is fenced by ACLs must not be handed back through the environment what
        # the ACLs deny it.
        env = dict(req.get("env") or {})
        try:
            proc = subprocess.Popen(req["cmd"], shell=True, cwd=req["cwd"], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
        except OSError as e:
            _send(conn, {"exit_code": 127, "stdout": "", "stderr": f"runner: {e}"})
            return
        stopped = threading.Event()

        def watch() -> None:  # the kernel says "stop" (the run is over) or hangs up
            try:
                msg = _recv(f)
            except (OSError, ValueError, IsolationError):
                msg = None
            if msg is None or msg.get("stop"):
                stopped.set()
                _kill(proc, signal.SIGTERM)
        threading.Thread(target=watch, daemon=True).start()
        deadline = time.monotonic() + float(req.get("timeout_s") or 3600)
        try:
            stdout, stderr = proc.communicate(timeout=max(0.1, deadline - time.monotonic()))
            code = proc.returncode
        except subprocess.TimeoutExpired:
            _kill(proc, signal.SIGKILL)
            stdout, _ = proc.communicate()
            code, stderr = 124, f"timed out after {req.get('timeout_s')}s"
        if stopped.is_set():
            _kill(proc, signal.SIGKILL)
        try:
            _send(conn, {"exit_code": code, "stdout": stdout, "stderr": stderr, "stopped": stopped.is_set()})
        except OSError:
            pass


def serve_runner(address: str, allowed_uids: set[int], stop: threading.Event | None = None,
                 server: socket.socket | None = None) -> None:
    """One thread per process: runs are long and several run at once."""
    srv = server or listen(address)
    srv.settimeout(0.5)
    with srv:
        while not (stop and stop.is_set()):
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            threading.Thread(target=handle_runner, args=(conn, allowed_uids), daemon=True).start()


def run(address: str, cmd: str, cwd: str, env: dict, timeout_s: float, alive=None,
        alive_every_s: float = 15.0) -> tuple[int, str, str]:
    """Start `cmd` through the runner and wait; `alive()` returning False stops it (the run's result is
    already in). `env` is added to the runner's own environment: the kernel's never crosses over."""
    s = connect(address, timeout=None)
    with s, s.makefile("rb") as f:
        _send(s, {"cmd": cmd, "cwd": cwd, "env": env, "timeout_s": timeout_s})
        stop_sent = False
        while not select.select([s], [], [], alive_every_s)[0]:  # select, not timeouts: a timed-out reader is dead
            if not stop_sent and alive and alive() is False:
                _send(s, {"stop": True})
                stop_sent = True
        resp = _recv(f)
    if resp is None:
        raise IsolationError("the runner closed the connection")
    if "error" in resp:
        raise IsolationError(resp["error"])
    err = resp["stderr"] + ("\n[stopped: the run's result was already in]" if resp.get("stopped") else "")
    return (0 if resp.get("stopped") else resp["exit_code"]), resp["stdout"], err


def _chunk(paths: list[str], budget: int = 100000) -> list[list[str]]:
    """Split a path list into argv-safe groups. `execve` allows one argument at most
    `MAX_ARG_STRLEN` (131072 bytes on this host) and a whole argv at most `SC_ARG_MAX`
    (2097152), so an unbounded spread raises an uncaught `OSError [Errno 7]`. Measured
    2026-10-24: `setup_plan` emitted one 2.2 MB argv (35,170 arguments) and
    `holon isolation apply` died on the step it had just instructed the Chair to run."""
    out, cur, size = [], [], 0
    for p in paths:
        # a single path already too long cannot be granted: say so rather than crash
        if len(p) > budget:
            raise IsolationError(f"a path is {len(p)} bytes, over the {budget}-byte argv budget")
        if cur and size + len(p) > budget:
            out.append(cur)
            cur, size = [], 0
        cur.append(p)
        size += len(p)
    if cur:
        out.append(cur)
    return out


def share_run_dir(path, agent_user: str) -> None:
    """A run directory the agent user may read and add its result to, but where it cannot replace what the
    kernel wrote: sticky, owned by the kernel, the agent granted rwx by ACL, kernel files readable only —
    and a directory the kernel wrote (the run's exported skills) readable recursively, never writable.
    The kernel's log is created now, so the agent cannot plant a symlink where the kernel later writes."""
    path = str(path)
    open(os.path.join(path, "driver.log"), "a").close()
    os.chmod(path, 0o1700)
    cmds = [["setfacl", "-m", f"u:{agent_user}:rwx", path]]
    files = []
    for entry in sorted(os.listdir(path)):
        if entry == "result.json":
            continue
        full = os.path.join(path, entry)
        if os.path.isdir(full) and not os.path.islink(full):
            cmds.append(["setfacl", "-R", "-m", f"u:{agent_user}:rX", full])
        else:
            files.append(full)
    if files:
        for group in _chunk(files):
            cmds.append(["setfacl", "-m", f"u:{agent_user}:r", *group])
    for cmd in cmds:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            raise IsolationError(f"{' '.join(cmd[:3])} failed: {p.stderr.strip()}")


# ── reading what an agent wrote ──────────────────────────────────────────
def read_agent_file(path) -> str | None:
    """A file an agent wrote (result.json), read without following a symlink it may have planted and
    only if it is a regular file of sane size. None if absent."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise IsolationError(f"{path}: refused ({e.strerror}: a symlink?)") from None
    with os.fdopen(fd) as fh:
        st = os.fstat(fh.fileno())
        if not (st.st_mode & 0o170000 == 0o100000) or st.st_size > MAX_FILE:
            raise IsolationError(f"{path}: not a regular file of at most {MAX_FILE} bytes")
        return fh.read()


# ── setting it up, and checking that it holds ─────────────────────────────
def _chain(path: Path, top: Path) -> list[Path]:
    """The directories from `top` down to (not including) `path`."""
    rel = path.relative_to(top)
    return [top.joinpath(*rel.parts[:i]) for i in range(len(rel.parts))]


def reach(k) -> dict:
    """What the agent user needs: trees it writes (the departments' reach, `[isolation] grants`, harness
    homes named in driver environments), trees it only reads (`[isolation] reads`, the pooled units'
    read-only roots, the knowledge bases, the kernel distribution, harness binaries) and the run
    directories."""
    from holon.execution import drivers
    c = k.config["isolation"]
    writes = {Path(r).expanduser() for r in c.get("grants") or []}
    reads = {Path(r).expanduser() for r in c.get("reads") or []} | {Path(__file__).resolve().parents[2]}
    # The knowledge bases are read-only to an agent: the kernel is their writer (one verb, one
    # commit, one seat), so the tree is granted as a read, never as a grant. Without this the
    # agent user cannot read the KB it is bound to — `holon kb run` still works, because the
    # kernel runs the vendored CLI, but a body that wants to read or diff its own KB is denied.
    reads.add(k.home.root / "kb")
    for s in k.scopes():
        writes |= {Path(r) for r in s["roots"]}
        reads |= {Path(r) for r in s.get("read_roots") or []}   # U2: pooled roots are reads, never the pen
    # A pooled unit's service is offered to every department that pools it, and the terminal fence
    # already names its entry-point token (`execution/service.py:1946`, BYLAWS §14) — so the agent
    # is told it may run `identity get` while nothing on its PATH resolves it and no grant covers
    # the binary. Measured 2026-10-24: the isolated runner's PATH is
    # `%h/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin`, so an out-of-tree tool's bare name is
    # `FileNotFoundError`. The grant is the read half of the fence the architecture already intends,
    # and it is read-only for the same reason a harness binary is (`:367-374` above): this is
    # executable code the agent runs, not a place to be selective.
    #
    # The predicate is `capabilities_for`'s, not `pooled_by`'s: a unit pooled `pool_of = ["*"]`
    # (org-wide, the identity case) has an edge with `other = '*'`, which `pooled_by`'s
    # `e.other = <slug>` never matches — so using it here would have granted *nothing* to the
    # most widely pooled units and everything to a unit pooled by nobody. `e.other = ? OR
    # e.other = '*'` is the same join `capabilities_for` and `service.scope` use, restricted to
    # the departments this deployment has, so an unpooled manifest widens nobody's reach.
    slugs = {s.get("name") for s in k.scopes()} | {None}
    from holon.bylaws import units as units_mod
    # `pool_of = ["*"]` (org-wide) or a named department this deployment has. The IN clause is
    # built only from the named slugs, so an empty department set still leaves the '*' arm.
    named = sorted(s for s in slugs if s)
    if named:
        rows = k.wconn.execute(
            "SELECT DISTINCT u.slug FROM unit_edges e JOIN units u ON u.slug = e.slug"
            " WHERE e.kind='pool' AND (e.other IN (" + ",".join("?" * len(named)) +
            ") OR e.other='*')", tuple(named)).fetchall()
    else:
        rows = k.wconn.execute(
            "SELECT DISTINCT u.slug FROM unit_edges e JOIN units u ON u.slug = e.slug"
            " WHERE e.kind='pool' AND e.other='*'").fetchall()
    for row in rows:
        for s in k.wconn.execute("SELECT command, health FROM unit_services WHERE slug=?",
                                 (row[0],)).fetchall():
            for key in ("command", "health"):
                raw = (s[key] or "").strip()
                if not raw:
                    continue
                token = raw.split()[0]
                found = units_mod.resolve_token(token)
                if found:
                    bin_path = Path(found).resolve()
                    reads.add(bin_path.parent)
                    reads.add(bin_path)
    for d in drivers.available(k.home).values():
        writes |= {Path(v) for v in d.env.values() if v.startswith("/") and Path(v).is_dir()}
        exe = d.command.split()[0] if d.command.split() else ""
        found = shutil.which(exe) if exe else None
        if found:
            # The harness's reach is the tree its command resolves into, not the one path
            # `shutil.which` returns. A launcher is a file that execs siblings from its own
            # directory and often sets LD_LIBRARY_PATH to it first (measured 2026-10-23 on the
            # live harness: a 505-byte launcher beside a 171 MB payload), so granting the
            # launcher alone denies the payload and the department cannot start its body.
            # The whole directory is granted as a read: a harness install is not a place to
            # be selective, and everything in it is executable code the agent is about to
            # run anyway. Its home (`[env]`, the writes above) stays the only writable part.
            bin_path = Path(found).resolve()
            reads.add(bin_path.parent)
            reads.add(bin_path)
    return {"writes": sorted(p.resolve() for p in writes if p.exists()),
            "reads": sorted(p.resolve() for p in reads if p.exists()), "runs": k.home.runs_dir.resolve()}


def setup_plan(k) -> dict:
    """The one-time setup: a script for root (the agent user and its runner service) and the ACLs the
    kernel's user applies (`holon isolation apply`). Inside the kernel user's home, which agents must
    never read, the agent may only traverse the directories leading to what it needs.

    On the security promise, precisely, because it was overstated (measured 2026-10-23): the plan
    emits an access deny for entries that are not in the reach, but a later recursive grant on a
    directory REPLACES a named deny on a file beneath it — POSIX ACLs hold one entry per user, and
    the last `setfacl` for that user wins. Measured on a real replica, the harness's payload is
    named with `-m u:<agent>:---` and then ends `user:<agent>:r--`, because the version directory
    it sits in is granted `rX` afterwards. So the deny is inert wherever a later grant covers the
    path, and the outcome the operator gets is the one the LAST command decides.

    What that means for a reader of the plan: a line naming a deny is not a guarantee. What it
    decides is the traversal grant on the directories leading to each target, and the final mode
    on every target. The two guards that keep the outcome honest are in
    tests/test_isolation_redteam.py — one checks the last applied mode matches what `reach()`
    classifies, the other applies this plan's own commands to a real tree and reads the resulting
    ACL. Both are mutation-verified."""
    import pwd
    iso = k.isolation
    proposed = ""
    if not iso or not iso.get("agent_user"):
        # The plan that tells you which user to create must be reachable from the state the
        # plan is needed in. Measured 2026-10-23: `isolation plan` refused with "set
        # [isolation] agent_user first", so BYLAWS §11's activation sequence (`plan` → `sudo sh`
        # → `apply --force` → broker → doctor) was broken at its first step — and the live
        # instance's boundary stayed absent for a week behind it. The name is proposed here,
        # the script writes it into holon.toml for you, and everything downstream
        proposed = f"holon-agent-{k.auth.instance_id}"
        # `holon-agent-<instance>`: `holon-agent` is the name every record uses, and the id
        # keeps several instances on one host unambiguous. A suggestion printed for the
        # operator to write into holon.toml, not a name the kernel keeps to itself.
    agent = (iso or {}).get("agent_user") or proposed
    me = pwd.getpwuid(os.getuid())
    top = Path(me.pw_dir).resolve()
    r = reach(k)
    targets = [(p, "rwX") for p in r["writes"]] + [(p, "rX") for p in r["reads"]] + [(r["runs"], "x")]
    chains: set[Path] = set()
    for p, _ in targets:
        if p.is_relative_to(top):
            chains |= set(_chain(p, top))
    keep = chains | {p for p, _ in targets}
    acl: list[list[str]] = []
    for d in sorted(chains):
        acl.append(["setfacl", "-m", f"u:{agent}:x", str(d)])  # -d applies to a whole command: two commands
        acl.append(["setfacl", "-d", "-m", f"u:{agent}:---", str(d)])
        others = [str(c) for c in sorted(d.iterdir()) if c not in keep and not c.is_symlink()]
        for group in _chunk(others):
            acl.append(["setfacl", "-m", f"u:{agent}:---", *group])
    for p, mode in targets:
        if mode == "x":
            acl.append(["setfacl", "-m", f"u:{agent}:x", str(p)])
            acl.append(["setfacl", "-d", "-m", f"u:{agent}:---", str(p)])
        elif p.is_dir():
            acl.append(["setfacl", "-R", "-m", f"u:{agent}:{mode}", str(p)])
            acl.append(["setfacl", "-R", "-d", "-m", f"u:{agent}:{mode}", str(p)])
        else:
            acl.append(["setfacl", "-m", f"u:{agent}:{mode.replace('X', 'x')}", str(p)])
    runner_addr = (iso or {}).get("runner") or f"@holon-runner-{k.auth.instance_id}"
    # The one file the sequence depends on must be the one file that cannot disappear. Measured
    # 2026-10-25 (the Chair's live run): the docs named `/tmp/holon-isolation.sh`, and `/tmp` is
    # tmpfs (gone per boot) *and* cleared by tmpfiles.d at 10 days — the script the sequence told
    # root to run was the one file guaranteed to be missing on the day it was needed, and nothing
    # re-created or checked for it. The plan is a deterministic function of instance state, so the
    # script is a cache of the plan, not a deliverable: it belongs under `$HOLON_HOME`, which
    # `holon backup` carries to the next machine, and regenerating it is one command.
    setup_script = k.home.root / "isolation" / "setup.sh"
    # When no agent user was named yet, root records the one it creates: the kernel's own config is
    # what every later step reads, and a boundary whose user exists only in a shell transcript is
    # one reboot from being forgotten.
    config_line = ("" if not proposed else
                   "echo 'write this into holon.toml, then: holon isolation apply --force'\n"
                   f"echo '[isolation]'\n"
                   f"echo 'agent_user = \"{agent}\"'\n"
                   f"echo 'runner = \"{runner_addr}\"'\n")
    unit = f"holon-runner-{k.auth.instance_id}"
    root = f"""#!/bin/sh
# Holon isolation: the part only root can do, once (docs/BYLAWS.md §11). Creates the agent user and the
# runner service that starts agent processes as that user for this instance's kernel (uid {me.pw_uid}).
set -eu
id -u {agent} >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/{agent} --shell /usr/sbin/nologin {agent}
{config_line}cat > /etc/systemd/system/{unit}.service <<'UNIT'
[Unit]
Description=Holon runner: agent processes as {agent} for {me.pw_name}'s instance {k.auth.instance_id}
After=network-online.target

[Service]
User={agent}
WorkingDirectory=/var/lib/{agent}
# No PYTHONPATH pinned to the planning checkout: the 2026-10-21 rule, applied here. The unit names
# no tree, so a moved or deleted worktree does not kill every agent process with nothing in the
# journal naming the cause.
# systemd's service PATH omits per-user bin dirs, where the `holon` launcher lives (the tick unit
# carries the same line for the same reason). Without it, `command -v holon` finds nothing below and
# the start shell prints `readlink: missing operand` -- and exits 0, so systemd records a healthy
# unit that never started a runner. Measured on 2026-10-23 under
# `env -i PATH=/usr/local/bin:/usr/bin:/bin`.
Environment=PATH=%h/bin:%h/.local/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=/bin/sh -c 'H=$(dirname $(dirname $(readlink -f $(command -v holon)))) && PYTHONPATH="$H" exec {sys.executable} -m holon.execution.isolation runner --address {runner_addr} --allow-uid {me.pw_uid}'
Restart=always
RestartSec=10
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now {unit}.service
"""
    return {"agent_user": agent, "proposed": proposed, "runner": runner_addr,
            "root_script": root, "acl": acl, "runner_unit": unit,
            "setup_script": str(setup_script), **{k_: [str(x) for x in v]
            for k_, v in (("writes", r["writes"]), ("reads", r["reads"]))}, "runs": str(r["runs"])}


def secrets(k) -> list[str]:
    """What an agent must never read: the Chair key and its directory's files, the databases, the config
    and the dashboard's credentials."""
    key = k.auth.chair_key_path()
    out = {key, k.home.work_db, k.home.memory_db, k.home.config_file}
    for d in {key.parent, key.parent.parent}:
        if d.is_dir():
            out |= {p for p in d.iterdir() if p.is_file()}
    out |= set(k.home.root.glob("adapters/notion/*.json"))
    return sorted(str(p) for p in out if p.exists())


def probe(k) -> dict:
    """Ask the runner, as the agent user, who it is and what it can reach (doctor's isolation checks)."""
    import shlex
    iso = k.isolation
    r = reach(k)

    def ask(test: str, paths: list, report_when: str) -> list[str]:  # in chunks: one shell argument is <128 KB
        out = []
        for i in range(0, len(paths), 200):
            q = " ".join(shlex.quote(str(p)) for p in paths[i:i + 200])
            _, o, _ = run(iso["runner"], f'for f in {q}; do test {test} "$f" {report_when} echo "$f"; done; true', "/",
                          {}, 60)
            out += [x for x in o.splitlines() if x]
        return out
    code, user, err = run(iso["runner"], "id -un", "/", {}, 30)
    return {"user": user.strip(), "error": err.strip() if code else "",
            "readable_secrets": ask("-r", secrets(k), "&&"), "unwritable": ask("-w", r["writes"], "||"),
            "unreadable": ask("-r", r["reads"], "||")}


def apply(plan: dict) -> list[str]:
    """Apply the ACL plan as the kernel's user; returns the commands that failed (e.g. an entry owned by
    another user), so the caller can say so. Re-running it is harmless."""
    failed = []
    for cmd in plan["acl"]:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode:
            failed.append(f"{' '.join(cmd)}: {p.stderr.strip()}")
    return failed


def main(argv: list[str] | None = None) -> int:
    """`python3 -m holon.execution.isolation runner --address @NAME --allow-uid UID`: the runner, started
    by its service as the agent user. It opens no instance: it only starts processes for the kernel."""
    import argparse
    ap = argparse.ArgumentParser(prog="holon-runner")
    ap.add_argument("role", choices=["runner"])
    ap.add_argument("--address", required=True)
    ap.add_argument("--allow-uid", type=int, action="append", required=True)
    a = ap.parse_args(argv)
    print(f"holon runner on {a.address} as uid {os.getuid()}, for uids {a.allow_uid}", flush=True)
    serve_runner(a.address, set(a.allow_uid))
    return 0


if __name__ == "__main__":
    sys.exit(main())
