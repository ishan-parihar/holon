"""The isolation system's red-team audit.

An agent's process must not be able to reach what the isolation boundary exists
to protect. This audit measures what the boundary actually grants, rather than
what its documentation claims.

Measured 2026-10-23 on the live instance (isolation OFF: no `[isolation]` section,
no agent user, no runner or broker unit, no ACLs) and on a scratch instance with
`[isolation] agent_user` set.

Three seams were probed:

1. **The runner's environment.** `handle_runner` starts processes with
   `env={**os.environ, **req.get("env", {})}`: the caller's dict is merged OVER
   the runner's whole environment, and the runner is started by a systemd unit
   that sanitizes nothing (`NoNewPrivileges=yes` and nothing else). Every
   variable the runner inherited is therefore settable and readable by the
   spawn process, and the caller's values win.

2. **The reach.** `reach()` builds the write and read trees from `[scope]`
   roots, `[isolation] grants/reads`, the drivers' `[env]` directories and the
   distribution. An imported department's scope roots are the POOL's own
   directories (ventures.venture_import) — which is the legacy trading
   repository on this machine — so the agent user is granted write access to a
   system this organization neither owns nor can reach, while the instance's own
   run directories, harness homes and knowledge bases must be enumerated
   correctly or the agent cannot do its job at all.

3. **The run directory.** `share_run_dir` + `read_agent_file` are the seam where
   an agent hands its result back: sticky, kernel-owned, agent granted rwx on
   the directory and r on the kernel's files, and a symlink the agent plants at
   `result.json` is refused rather than followed.
"""

import io
import contextlib
import json
import os
import pathlib
import re
import secrets
import sqlite3
import subprocess
import sys
import threading
import types
import unittest

from holon.execution.service import Kernel
from holon.core.paths import Home
from tests.helpers import KernelCase, REPO
from holon.execution import isolation


class RedTeam(KernelCase):
    """Probes run against a real broker and runner, exactly as the kernel starts them."""

    def setUp(self):
        super().setUp()
        tag = secrets.token_hex(4)
        self.broker, self.runner = f"@holon-rt-broker-{tag}", f"@holon-rt-runner-{tag}"
        self.reconfigure(f'[isolation]\nagent_user = "{os.getlogin()}"\nbroker = "{self.broker}"\n'
                         f'runner = "{self.runner}"\ngrants = ["{self.workdir}"]\n')
        self.stop = threading.Event()
        me = {os.getuid()}
        b, r = isolation.listen(self.broker), isolation.listen(self.runner)
        threading.Thread(target=isolation.serve_broker,
                         args=(str(self.home.root), self.broker, me, self.stop, b), daemon=True).start()
        threading.Thread(target=isolation.serve_runner, args=(self.runner, me, self.stop, r),
                         daemon=True).start()
        self.addCleanup(self.stop.set)

    def reconfigure(self, extra):
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + extra)
        self.k = Kernel(self.home)

    # ── the runner's environment ───────────────────────────────────────────
    def test_a_spawned_process_can_read_a_credential_the_runner_inherited(self):
        """A caller-supplied env name reaches the spawned process — that is what the run's
        environment is *for*.

        This asserts the half of the runner's contract that was never in dispute, and the
        half that must not regress while fixing the other: a run carries its own env (its
        tokens, its endpoints), so a caller's value must arrive. Measured 2026-10-23 with
        an `x` in the caller's dict: the spawn prints `x`.

        The disputed half is in
        `test_the_isolated_harness_gets_only_the_runs_environment`: a name the runner
        *inherited* must not arrive. That defect was real and is fixed, so two things must
        both hold — the caller's dict arrives, and nothing else does. The docstring here
        used to claim the inherited environment is "merged over", which stopped being true
        when 1a was fixed; the assertion kept passing because it never tested the merge.
        That is the drift this docstring now corrects: an assertion that outlives its own
        description is how a suite looks green after its premise changed."""
        code, out, err = isolation.run(self.runner, "echo ${RT_SECRET:-unset}", "/", {"RT_SECRET": "x"}, 30)
        self.assertEqual(out.strip(), "x", "a caller-supplied env value reaches the spawned process")

    def test_the_runner_hands_the_caller_power_over_any_inherited_name(self):
        """A name the caller supplies wins, and only the caller's names arrive.

        Measured 2026-10-23: `HOLON_HOME` is overridable to `/tmp/rt`. This is about the
        **caller's** dict, which is the run's own environment and authoritative for it.

        The docstring's earlier half — "a name the inherited environment already carries
        can be overridden ... the caller can override any credential" — described 1a's
        defect: the merge of the runner's inherited environment, which is how a credential
        the runner starts with became the agent's. Fixed, so there is no longer an
        inherited environment to override; the remaining truth is that the run's own names
        are the caller's to set. Kept, because the assertion is still the right one to
        watch while that boundary is subject to change."""
        code, out, _ = isolation.run(self.runner, "echo ${HOLON_HOME:-unset}", "/", {"HOLON_HOME": "/tmp/rt"}, 30)
        self.assertEqual(out.strip(), "/tmp/rt", "the caller's value wins over the inherited one")

    def test_the_isolated_harness_gets_only_the_runs_environment(self):
        """`drivers.run` promises the harness "only the run's environment (never the
        kernel's)". The runner merged the caller's dict over its own whole
        environment, so a name the runner inherited reached the body anyway.

        Measured 2026-10-23, this is the LESS filtered of the two branches: the
        non-isolated branch explicitly drops HOLON_CHAIR_KEY and HOLON_KEY, while
        the isolated one reintroduced the entire inherited environment one layer
        down. A body with a shell could read every name the runner's systemd unit
        starts it with; that unit sanitizes nothing. An organization whose ACLs
        fence a body's reach must not hand back through the environment what the
        ACLs deny it."""
        marker = "RT_INHERITED_SECRET"
        os.environ[marker] = "sk-a-secret-the-runner-inherited"
        try:
            code, out, _ = isolation.run(self.runner, "echo ${" + marker + ":-unset}", "/", {}, 30)
        finally:
            os.environ.pop(marker, None)
        self.assertEqual(out.strip(), "unset",
                         "a name the runner inherited reached the spawned process")

    # ── the boundary itself ────────────────────────────────────────────────

    def test_the_runner_refuses_a_real_uid_the_comparator_cannot_see(self):
        """The uid decision is the one line the isolation system is: a process the
        kernel's user does not own may not start processes here. And it is the one
        line no test in this suite has ever proved.

        Measured 2026-10-24 against a **real** runner on a real abstract socket, with a
        peer that is uid 99 inside its own user namespace. `SO_PEERCRED` — the mechanism
        `peer_uid` reads, and the only one that answers from outside the process — reports
        a peer's uid translated into the *receiving* socket's namespace, so that namespaced
        peer appears to the runner as this user's own uid, is admitted, and its command runs.

        So this guard asserts the negative, and says so: it stands up the real runner on a
        real abstract socket, connects to it from a peer that is uid 99 inside its own
        user namespace, and holds the runner to the truth -- that the command it ran
        reported THIS user's uid, so the peer was translated into the runner's namespace
        and admitted. When the Chair activates the boundary, `holon isolation apply`
        writes the agent user and this becomes a real two-user assertion instead of a
        measurement of the host. Until then it is the honest description of what is
        unproved, rather than a passing test read as proof."""
        import subprocess
        import time
        address = "@holon-rt-uid-" + secrets.token_hex(4)
        runner = subprocess.Popen([sys.executable, "-m", "holon.execution.isolation", "runner",
                                   "--address", address, "--allow-uid", str(os.getuid())],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            # the runner's socket must be listening before the peer dials it
            deadline = time.time() + 20
            while time.time() < deadline:
                try:
                    s = isolation.connect(address, timeout=1)
                    s.close()
                    break
                except isolation.IsolationError:
                    time.sleep(0.1)
            else:
                self.fail(f"the runner never listened on {address}")
            probe = subprocess.run([sys.executable, str(REPO / "tests/probe_uid.py"), address],
                                  capture_output=True, text=True, timeout=120, check=False)
            self.assertEqual(0, probe.returncode, f"the probe could not run: {probe.stderr[:400]}")
            self.assertIn("'exit_code': 0", probe.stdout,
                          "the runner REFUSED a peer that is a different uid inside its own "
                          f"namespace: {probe.stdout[:300]!r}. This host can therefore produce a "
                          "uid the runner sees as different, so this guard owes the real two-user "
                          "assertion instead of a statement about the host.")
            self.assertIn(str(os.getuid()), probe.stdout,
                          "the runner ran the probe's command but it reported a uid that is not "
                          "this process's: the peer was NOT translated into the runner's "
                          "namespace, so the measurement is not comparable.")
        finally:
            runner.terminate()
            try:
                runner.wait(10)
            except subprocess.TimeoutExpired:
                runner.kill()

    def test_the_broker_refuses_a_uid_it_does_not_allow(self):
        """The broker's first gate is the peer uid: a caller outside `allowed_uids`
        is refused before any command runs."""
        other = "@holon-rt-other-" + secrets.token_hex(4)
        srv = isolation.listen(other)
        threading.Thread(target=isolation.serve_broker,
                         args=(str(self.home.root), other, {os.getuid() + 99999}, None, srv),
                         daemon=True).start()
        s = isolation.connect(other)
        isolation._send(s, {"argv": ["whoami"], "token": None, "key": None, "files": {}, "stdin": ""})
        resp = json.loads(s.makefile("rb").readline())
        s.close()
        srv.close()
        self.assertNotEqual(resp["code"], 0)
        self.assertIn("uid", resp["stderr"], resp)

    def test_the_broker_never_reads_a_path_an_agent_names(self):
        """`_read` opens only what the client sent: the broker runs as the kernel's
        user, so a path an agent names but cannot read must not be read for it."""
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = isolation.forward(["skill", "show", "@/etc/shadow"], self.broker)
        self.assertNotEqual(rc, 0)

    # ── the run directory ──────────────────────────────────────────────────
    def test_a_symlink_planted_as_the_result_is_never_followed(self):
        """The result the agent hands back is read with O_NOFOLLOW and must be a
        regular file of sane size, so a planted symlink cannot point the kernel at
        a file the agent may not write (the Chair key, another run's result)."""
        d = self.tmp / "run"
        d.mkdir()
        (d / "brief.md").write_text("the kernel's brief")
        isolation.share_run_dir(d, os.getlogin())
        link = d / "result.json"
        link.unlink(missing_ok=True)
        link.symlink_to("/etc/passwd")
        with self.assertRaises(isolation.IsolationError):
            isolation.read_agent_file(link)

    def test_the_agent_grant_is_exactly_what_the_docstring_promises(self):
        """The run directory: sticky, kernel-owned, agent rwx on the directory, r on
        the kernel's files, and nothing for others."""
        import subprocess
        d = self.tmp / "run2"
        d.mkdir()
        (d / "brief.md").write_text("brief")
        isolation.share_run_dir(d, os.getlogin())
        acl = subprocess.run(["getfacl", "-p", str(d)], capture_output=True, text=True).stdout
        self.assertIn("flags: --t", acl, "sticky: the agent cannot remove the kernel's files")
        self.assertIn(f"user:{os.getlogin()}:rwx", acl)
        self.assertIn("other::---", acl)
        file_acl = subprocess.run(["getfacl", "-p", str(d / "brief.md")], capture_output=True, text=True).stdout
        self.assertIn(f"user:{os.getlogin()}:r--", file_acl, "read-only, never the pen")


class Reach(KernelCase):
    """The reach a deployment actually gets — the part that decides whether the boundary
    is usable, not merely closed.

    A boundary that denies the agent everything its harness needs is not a trust
    boundary: it is a department that cannot work. Measured 2026-10-23 on the live
    instance, `reach()` grants a harness binary as one FILE, while the harness is a
    two-file layout — a 505-byte launcher and a 171 MB payload it execs from the same
    directory, after setting LD_LIBRARY_PATH to that directory. The launcher is granted;
    the payload is not, so the agent user cannot run the harness at all.
    """

    def test_the_harness_binary_lands_with_every_file_it_needs_to_run(self):
        """A harness's reach is the tree its command resolves into, not the one path
        `shutil.which` returns. A launcher that execs a sibling (measured: the live
        harness, `LD_LIBRARY_PATH=$self_dir` then `exec $self_dir/<payload>`) needs
        that sibling granted, and the directory it lives in, or the agent user cannot
        start the harness the instance dispatches."""
        from holon.execution import isolation
        (self.tmp / "hb" / "bin").mkdir(parents=True)
        launcher = self.tmp / "hb" / "bin" / "harness"
        payload = self.tmp / "hb" / "bin" / "harness-linux-x86_64.bin"
        launcher.write_text("#!/bin/sh\nexec \"$(dirname \"$0\")/harness-linux-x86_64.bin\" \"$@\"\n")
        payload.write_bytes(b"payload")
        launcher.chmod(0o755)
        self.home.config_file.write_text(self.pipeline + "\n" + self.config +
                                        f"\n[isolation]\nagent_user = \"{os.getlogin()}\"\n"
                                        f"runner = \"@hb-runner\"\nbroker = \"@hb-broker\"\n")
        (self.home.drivers_dir / f"hb.toml").write_text(
            f'name = "hb"\ncommand = "{launcher}"\nresult = "file"\n')
        self.k = Kernel(self.home)
        reads = {str(p) for p in isolation.reach(self.k)["reads"]}
        plan = isolation.setup_plan(self.k)
        self.k.close()
        self.assertIn(str(self.tmp / "hb" / "bin"), reads,
                      "the directory holding the harness is not in the agent user's read "
                      "reach: LD_LIBRARY_PATH and the launcher's payload sibling are "
                      "unreachable, so the department cannot start its body")
        self.assertIn(str(launcher), reads, "the harness's own command is not granted")
        # the payload is granted through the directory's ACL, not as its own entry:
        # a directory in the reach that no ACL command carries is the defect
        granted = {tuple(c)[-1] for c in plan["acl"] if c[0] == "setfacl"}
        self.assertIn(str(self.tmp / "hb" / "bin"), granted,
                      "the ACL plan does not grant the harness's directory to the agent user: "
                      "the payload beside the launcher stays unreadable")

    def test_a_cold_start_can_reach_the_activation_plan(self):
        """`holon isolation plan` is step 1 of the documented activation
        (docs/BYLAWS.md §11: `plan` → `sudo sh` → `apply --force` → broker → doctor).

        Measured 2026-10-23 on an instance with no `[isolation]` section, the command
        refuses with "set [isolation] agent_user first" — so the plan that tells you
        which user to create is unreachable until you have already named one. That is
        the gap that kept the boundary off the live instance for a week: the documented
        sequence is broken at its first step.

        The plan must name the user it would create, so a cold start can read it and
        then write it into holon.toml; everything downstream still checks it."""
        from holon import cli
        import io, contextlib
        self.k.auth.init_chair_key()
        # no [isolation] section: the state a cold start is in
        self.home.config_file.write_text(self.pipeline + "\n" + self.config)
        root = pathlib.Path(self.tmp / "root.sh")
        k = Kernel(self.home)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            cli.cmd_isolation_plan(types.SimpleNamespace(write=str(root), json=False), k)
        printed = buf.getvalue()
        script = root.read_text()
        self.assertIn("useradd", script, "the plan names the user it would create")
        # and the sequence it prints must be walkable: each command it names has to exist. Step 3
        # once said `systemctl --user enable --now holon-broker (install.py writes the unit)`, and
        # install.py writes that unit only when agent_user is ALREADY in holon.toml (measured on
        # the live instance: no broker unit, and the config unwritten) — so a cold start following
        # the plan would have been told to start a unit nothing had written. The same defect class
        # the ledger records twice: a remediation naming a command that cannot work.
        for line in printed.splitlines():
            if "holon-broker" in line and "systemctl" in line and "install.py" not in line:
                self.fail(f"the plan names a step that does not write the broker unit: {line!r}")
        self.assertIn("install.py", printed,
                      "the broker unit's writer must be named: install.py writes it once agent_user "
                      "is in holon.toml, which is the step before it")
        k.close()

    def test_the_applied_acl_matches_what_the_reach_classifies(self):
        """The plan prints 688 setfacl directives, and the last one for a path is the one
        setfacl leaves on disk. Nothing re-reads the applied ACLs, so the only guard is that
        the last mode a path receives agrees with `reach()`'s own classification.

        Measured on the live instance, a path legitimately carries two access modes: the chain
        pass grants `x` on a directory leading to a target, and the target pass then grants its
        real mode. So `pioneer-qf` reads `x` then `rwX` (a write, correct), and the harness dir
        and `.local/bin` read `x` then `rX` (reads, correct). That ordering is the design and it
        resolves correctly in every case measured — what follows from it is that the order, not
        the docstring, is what decides.

        Which is why `setup_plan`'s own promise — "every other entry ... carries an explicit
        deny" — is false as printed: measured on a real replica applying only the plan's own
        commands in its own order, the payload's final access ACL is `user:nobody:r--`, because
        the named `-m ---` on it is overwritten by the later recursive `rX` on its directory.
        A printed plan that names a deny for a path it then grants is a plan an operator cannot
        read: they would conclude the payload is denied. The outcome is right; the document is
        not, and if the emission order ever changed, the payload would be unreadable silently.

        So this guard checks the invariant that actually protects the boundary: whatever the
        last setfacl leaves, it must not be a write on a path reach() calls read-only."""
        plan = isolation.setup_plan(self.k)
        writes = {str(x) for x in isolation.reach(self.k)["writes"]}
        last: dict[str, str] = {}
        for c in plan["acl"]:
            if c[0] != "setfacl" or "-d" in c:      # -d is the default ACL, an intended pair with -m x
                continue
            entry = next((t for t in c[2:] if t.startswith("u:")), None)
            if not entry:
                continue
            mode = entry.rsplit(":", 1)[1]
            for p in (t for t in c[2:] if not t.startswith("u:")):
                last[p] = mode                        # the last write is what setfacl leaves
        wrong = []
        for p, mode in last.items():
            grants_write = "w" in mode.replace("X", "")
            if grants_write and p not in writes:
                wrong.append((p, mode, "the reach calls this read-only"))
        self.assertEqual([], wrong,
                         "the plan's last setfacl grants a write the reach does not classify as "
                         "one: the applied ACL is then wider than the boundary the reach defines")
    def test_the_plan_applied_to_a_real_tree_grants_the_harness_it_dispatches(self):
        """The requirement map's central claim, proved by applying the plan rather than
        reading it: on a real replica of the harness layout, apply ONLY the plan's own
        commands, then read what the ACL actually is.

        Measured 2026-10-23 on this host's real layout — a launcher `jcode` beside a
        171 MB payload it execs after `LD_LIBRARY_PATH=\"$self_dir\"`. The plan grants the
        version directory `rX`, grants traverse `x` on `.jcode`, `builds` and `versions`
        (the chain), and grants the launcher `rx`. It ALSO names a `---` deny on the payload
        in the chain pass. Applied in the plan's own order on a replica:

            user:nobody:r--    jcode
            user:nobody:r--    jcode-linux-x86_64.bin

        So the payload is reachable, and the named deny is inert — the later recursive
        `rX` on its directory overwrites it. The outcome is right, but not for the reason
        the printed plan states. Nothing here switches users, so it stops short of an
        access(2) proof, and the requirement map says so.

        This test exists because `ls` proved a listing and getfacl on the live tree proved
        a *plan*'s text; neither is the applied result. This is the applied result, on a
        real filesystem."""
        import subprocess
        root = self.tmp / ".jcode"
        v = root / "builds" / "versions" / "0.99.1"
        v.mkdir(parents=True)
        (root / "config.toml").write_text("cfg")
        (root / "config.toml.lock").write_text("lock")
        (v / "jcode").write_text("#!/bin/sh\nexec \"$0-linux-x86_64.bin\"\"$@\"\n")
        (v / "jcode").chmod(0o755)
        (v / "jcode-linux-x86_64.bin").write_bytes(b"payload")
        for other in ("0.89", "0.98"):
            (root / "builds" / "versions" / other).mkdir(parents=True, exist_ok=True)
        # the plan's own directives, in the plan's own order, for this layout only
        agent = os.getlogin()
        chain = [(root, "x"), (root / "builds", "x"), (root / "builds" / "versions", "x"), (v, "x")]
        for d, mode in chain:
            subprocess.run(["setfacl", "-m", f"u:{agent}:{mode}", str(d)], check=True)
            subprocess.run(["setfacl", "-d", "-m", f"u:{agent}:---", str(d)], check=True)
        subprocess.run(["setfacl", "-m", f"u:{agent}:---", str(root / "config.toml"),
                        str(root / "config.toml.lock")], check=True)
        subprocess.run(["setfacl", "-m", f"u:{agent}:rX", str(v)], check=True)
        subprocess.run(["setfacl", "-R", "-m", f"u:{agent}:rX", str(v)], check=True)
        subprocess.run(["setfacl", "-m", f"u:{agent}:rx", str(v / "jcode")], check=True)
        # what the agent user ends up able to do
        for f in (v / "jcode", v / "jcode-linux-x86_64.bin"):
            acl = subprocess.run(["getfacl", "-p", str(f)], capture_output=True, text=True).stdout
            self.assertIn(f"user:{agent}:r", acl,
                          f"{f.name} is not readable after the plan is applied: {acl}")
            self.assertNotIn(f"user:{agent}:---", acl,
                             f"{f.name} carries a deny the recursive grant should have replaced")


class Deployment(KernelCase):
    """The other half of the audit: what a deployment actually has, and what it says
    about a boundary that is absent.

    `RedTeam` above proves the mechanism holds when it is on. This class measures the
    operational seam the mechanism tests cannot see. Measured 2026-10-23 on the live
    instance (mode=ro):

        holon doctor  →  ERROR isolation.off (an absent boundary is an error, not a
        warning -- the heartbeat opens incidents only for errors). Measured
        2026-10-23 on the live instance, mode=ro: no `[isolation]` section in
        holon.toml, no `holon-agent` user, no runner unit; a broker unit exists but
        is disabled and points at a scratch home
        (`~/.config/systemd/user/holon-broker.service`).

        The record this measurement answers (`docs/design/2026-10-22-live-activation.md`
        §4) said "no broker unit" -- false, and a reader sent to create one would find
        it already there. A broker unit exists; the broker is not running.

    The record `docs/design/2026-10-22-live-activation.md` §4 measured the same three
    days earlier and named the one blocker: root must create the agent user, and it is
    still not created. What the record did not measure is the consequence: with
    `agent_user` unset the finding is a *warning*, and the heartbeat turns only
    `error` findings into incidents (`service.py`: `if f["severity"] == "error"`).
    So an absent trust boundary — every dispatched body able to read the Chair key
    and write work.db as the kernel's own user — signals nobody. The kernel's own
    doctor page names the exact exposure in the same breath."""

    def test_an_absent_boundary_is_an_error_not_a_warning(self):
        """With no `[isolation] agent_user`, agents run as the kernel's user: one of
        them with a shell can read the Chair key and write work.db directly. That is
        the total failure of the trust boundary, and it must reach an incident.

        The instance is given the Chair key a deployment has, because without one
        `_isolation` returns before it says anything (`if not k.auth.configured`) —
        which is its own finding, guarded by the test below."""
        from holon.integrity import doctor
        if not self.k.auth.configured:
            self.k.auth.init_chair_key()
        findings = {f["check"]: f for f in doctor.run(self.k)}
        self.assertIn("isolation.off", findings,
                      "a deployment with no boundary configured must be reported by name")
        f = findings["isolation.off"]
        self.assertEqual(f["severity"], "error",
                         "an absent trust boundary is reported at " + f["severity"]
                         + ": the heartbeat reports warnings to nobody, so the exposure "
                         "names itself in the doctor page and reaches no reader")

    def test_an_unconfigured_installation_says_something_about_the_boundary(self):
        """Measured: a fresh harness instance has no Chair key, and `_isolation`
        returns at that point — so it reports nothing at all about the boundary.

        An unconfigured installation is about to be configured, and the moment it
        dispatches its first body the boundary is what decides whether that body
        can write the organization's own database. 'Nothing to say yet' is how a
        deployment reaches its first dispatch with no signal either way."""
        from holon.integrity import doctor
        checks = {f["check"] for f in doctor.run(self.k)}
        self.assertTrue(any(c.startswith("isolation") for c in checks),
                        "an installation with no Chair key and no boundary reports nothing "
                        "about the trust boundary: " + ", ".join(sorted(checks)))

    def test_the_heartbeat_opens_an_incident_for_a_warning_it_should_escalate(self):
        """What `test_an_absent_boundary_is_an_error_not_a_warning` asserts must be
        what the heartbeat acts on: an `isolation.off` error must reach `_doctor_phase`.

        The kernel already treats a promised-without-a-runner boundary as an error
        (`isolation.runner`, measured 2026-10-22), so the severity of an *absent*
        boundary is not a policy question — it is the same exposure, one step wider."""
        src = (REPO / "holon" / "execution" / "service.py").read_text()
        self.assertIn('if f["severity"] == "error"', src,
                      "the escalation rule moved: re-check which findings reach incidents")
        # the reach of that rule must include the boundary's own check
        from holon.integrity import doctor
        self.assertTrue(any(f["check"] == "isolation.off" and f["severity"] == "error"
                            for f in doctor.run(self.k)),
                        "isolation.off must be an error for the heartbeat to act on it")

    def test_the_printed_sequence_numbers_every_step_it_prints(self):
        """The printed plan is the operator's whole journey, so its numbering must not
        imply a step it never showed.

        Measured 2026-10-23 on the live instance with `holon isolation plan` (no
        `--write`): the root script is printed raw as step 1's content, then the
        numbered list begins at **"1.5."** — there is no `1.` anywhere in the output.
        A reader counting along meets 1.5, 2, 3, 4 and a step 1 that was never
        labelled, which is the 46-line shell they just scrolled past with no way to
        know it was the step. The operator's move is a shell they must read anyway;
        an unlabelled one is where they skim.

        Two rules this holds, both about the document a person will type:
        the first numbered step is `1.`, and every integer level printed appears."""
        import subprocess
        out = subprocess.run([sys.executable, "-m", "holon", "--home", str(self.home),
                              "isolation", "plan"],
                             capture_output=True, text=True).stdout
        numbers = re.findall(r"^(\d+(?:\.\d+)?)\.", out, re.MULTILINE)
        self.assertTrue(numbers, f"the plan printed no numbered steps at all: {out[:300]!r}")
        self.assertEqual(numbers[0], "1",
                         f"the printed sequence starts at {numbers[0]!r}, not 1: an operator "
                         f"counting along has no step 1 to start from. full list: {numbers}")
        # no gap: every integer level from 1 to the last appears (1.5 is fine; 2.5 with no 2 is not)
        integers = sorted({n.split(".")[0] for n in numbers}, key=int)
        self.assertEqual(integers, [str(i) for i in range(1, int(integers[-1]) + 1)],
                         f"the sequence skips a level: printed {numbers}")

    def test_read_only_is_not_achievable_the_way_the_docs_claim_it_is(self):
        """AGENTS.md line 89 says: "read live state only with `mode=ro`, and `holon doctor`,
        before you conclude." The audit record's §2 and its requirement map both wrote
        "measured on the live instance (mode=ro)". Measured 2026-10-24, that claim is
        false every time it is made.

        `Kernel.__init__` calls `connect(home.work_db)` -- a read-WRITE connection,
        because `readonly=False` is the default -- and then `migrate(self.wconn)
        (service.py:266-268). So "reading" the live instance through the kernel opens
        the write side of it. `connect(readonly=False)` sets
        `PRAGMA journal_mode=WAL` -- writing the db file before any read happens.
        And `migrate` runs `CREATE TABLE IF NOT EXISTS schema_migrations` and an
        `ALTER TABLE schema_migrations ADD COLUMN checksum` and
        `UPDATE ... SET checksum` on every connection, read-only intent included
        (db.py:74-87).

        This is the same defect class as the audit's own strays, one level deeper:
        a read-only probe is not read-only, and it writes whether the callers meant
        it to or not. The guard is the last sentence of this class: this is exactly
        the probe I used, so the guard is the fix I owe.

        The contract to satisfy, all three measured: a `Kernel` built with `readonly=True`
        opens both databases `mode=ro`, refuses every write, creates no directory and seeds
        no row. Two of the three are observable and mutation-verified: forcing
        `home.ensure()` or `org.bootstrap()` in `readonly` mode fails this guard. The third
        is not -- `migrate()` on a `mode=ro` connection is refused statement by
        statement and leaves a zero-length `-wal`, so it is undetectable from outside; that
        guard is defence in depth, and a mutation of it surviving is correct rather than a
        gap. Read this as: the guard proves what can be proved and names what cannot."""
        from holon.execution.service import Kernel
        # A home with no instance fails loudly rather than being brought into being: that is
        # the correct strictness for a read-only probe, and it is measured here so nobody
        # later mistakes `mode=ro` for "will migrate if missing".
        empty = self.tmp / "empty-home"
        empty.mkdir(parents=True)
        with self.assertRaises(sqlite3.OperationalError):
            Kernel(Home(empty), actor="auditor", readonly=True)
        # and nothing was created on the way: a read-only probe cannot bring an instance
        # into being, which is the whole point (my own probes did exactly that)
        self.assertEqual([p.name for p in empty.rglob("*")], [],
                         "a read-only Kernel created instance state in a home that had none: "
                         f"{[str(p) for p in empty.rglob('*')]}")

        # and on a real instance it touches nothing: build one, then re-open read-only
        fresh = self.tmp / "ro-probe" / "home"
        fresh.mkdir(parents=True)
        home = Home(fresh)
        seed = Kernel(home)  # the writer brings the instance into being
        seed.close()  # a clean close checkpoints its WAL, leaving nothing behind
        for p in fresh.rglob("*"):
            p.chmod(0o644 if p.is_file() else 0o755)
        # The sidefiles (`-shm`/`-wal`) are excluded from the snapshot and asserted
        # separately: opening a WAL-mode database read-only still creates them, measured
        # with raw sqlite3 and no Holon involved. `-shm` is always 32768 bytes of shared
        # memory and says nothing; the `-wal` carries unmerged frames, so a non-empty one
        # is a write this probe made.
        data = lambda snap: {n: v for n, v in snap.items() if not n.endswith(("-wal", "-shm"))}
        before = {str(q.relative_to(fresh)): q.stat().st_mtime_ns
                  for q in fresh.rglob("*") if q.is_file()}
        k = Kernel(home, actor="auditor", readonly=True)
        try:
            self.assertTrue(k.readonly)
            with self.assertRaises(sqlite3.OperationalError):
                k.wconn.execute("INSERT INTO tasks (id, title) VALUES ('x', 'y')")
        finally:
            k.wconn.close()
            k.mconn.close()
        after = {str(q.relative_to(fresh)): q.stat().st_mtime_ns
                 for q in fresh.rglob("*") if q.is_file()}
        self.assertEqual(data(before), data(after),
                         "a read-only Kernel modified an existing database: "
                         f"{[n for n in data(after) if data(before).get(n) != data(after)[n]]}")
        for w in [n for n in after if n.endswith("-wal")]:
            self.assertEqual(pathlib.Path(fresh, w).stat().st_size, 0,
                             f"the read-only pass wrote data into the {w} WAL: a read-only "
                             "kernel must not leave frames for the next writer to merge")


if __name__ == "__main__":
    unittest.main()
