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
import secrets
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
        """The runner starts a process with the runner's whole environment plus the
        caller's dict merged over it. A credential in the runner's environment is
        therefore inside the spawn, and the caller can override any name.

        The systemd unit that starts the runner sanitizes nothing beyond
        NoNewPrivileges, so in a deployment where the operator's session
        environment reaches the service, every such name is the agent's.
        """
        code, out, err = isolation.run(self.runner, "echo ${RT_SECRET:-unset}", "/", {"RT_SECRET": "x"}, 30)
        self.assertEqual(out.strip(), "x", "a caller-supplied env value reaches the spawned process")

    def test_the_runner_hands_the_caller_power_over_any_inherited_name(self):
        """A name the inherited environment already carries can be overridden by the
        caller: the merge is the caller over the runner, so HOLON_HOME, PYTHONPATH
        and any credential are all replaceable."""
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
        from holon.integrity import doctor
        self.k.auth.init_chair_key()
        # no [isolation] section: this is the state a cold start is in
        self.home.config_file.write_text(self.pipeline + "\n" + self.config)
        k = Kernel(self.home)
        out = pathlib.Path(self.tmp / "root.sh")
        from holon import cli
        plan = next(c for c in cli.REGISTRY if c.path == ("isolation", "plan"))
        args = types.SimpleNamespace(write=str(out), json=False)
        cli.cmd_isolation_plan(args, k)          # must not raise
        k.close()
        script = out.read_text()
        self.assertIn("useradd", script, "the plan names the user it would create")


class Deployment(KernelCase):
    """The other half of the audit: what a deployment actually has, and what it says
    about a boundary that is absent.

    `RedTeam` above proves the mechanism holds when it is on. This class measures the
    operational seam the mechanism tests cannot see. Measured 2026-10-23 on the live
    instance (mode=ro):

        holon doctor  →  WARN isolation.off · 0 errors, 1 warnings
        no [isolation] section in holon.toml; no `holon-agent` user; no runner unit;
        no broker unit (the tick and the mirror only).

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


if __name__ == "__main__":
    unittest.main()
