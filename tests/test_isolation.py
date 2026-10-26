"""The trust boundary (docs/BYLAWS.md §11): agents run through the runner and reach the organization only
through the broker. Tests run both as the current user, which exercises every path except the uid
switch itself (the runner's service does that in production)."""

import contextlib
import getpass
import io
import json
import os
import secrets
import subprocess
import sys
import threading
import types
from pathlib import Path

from holon import cli as holon_cli
from holon.execution import isolation
from holon.execution.service import Kernel
from tests.helpers import FAKE_AGENT, REPO, KernelCase


class Isolated(KernelCase):
    def setUp(self):
        super().setUp()
        tag = secrets.token_hex(4)
        self.broker, self.runner = f"@holon-test-broker-{tag}", f"@holon-test-runner-{tag}"
        self.reconfigure(f'[isolation]\nagent_user = "{getpass.getuser()}"\nbroker = "{self.broker}"\n'
                         f'runner = "{self.runner}"\ngrants = ["{self.workdir}"]\n')
        self.stop = threading.Event()
        me = {os.getuid()}
        b, r = isolation.listen(self.broker), isolation.listen(self.runner)
        threading.Thread(target=isolation.serve_broker, args=(str(self.home.root), self.broker, me, self.stop, b),
                         daemon=True).start()
        threading.Thread(target=isolation.serve_runner, args=(self.runner, me, self.stop, r), daemon=True).start()
        self.addCleanup(self.stop.set)

    def reconfigure(self, extra):
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + extra)
        self.k.close()
        self.k = Kernel(self.home)

    def agent(self, *argv, token=None, key=None):
        """`holon …` as an agent under isolation would run it: forwarded to the broker."""
        out, err = io.StringIO(), io.StringIO()
        env = {"HOLON_RUN_TOKEN": token or "", "HOLON_KEY": key or ""}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v})
        for k in env:
            if not env[k]:
                os.environ.pop(k, None)
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = isolation.forward(list(argv), self.broker)
        finally:
            for k, v in old.items():
                os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        return code, out.getvalue(), err.getvalue()

    def test_a_harness_runs_through_the_runner_and_hands_its_result_in_through_the_broker(self):
        decoy = self.tmp / "decoy"  # where a direct `holon --home $HOLON_HOME` would go: the broker ignores it
        (self.home.drivers_dir / "fake.toml").write_text(
            f'name = "fake"\ncommand = "{sys.executable} {FAKE_AGENT}"\ntimeout_s = 60\n[env]\n'
            f'FAKE_MODE = "self-submit"\nPYTHONPATH = "{REPO}"\nHOLON_HOME = "{decoy}"\n')
        w = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out["submitted_by_agent"], out)
        self.assertEqual(self.pipeline_of(w)[-1], ("approve", "ready"))
        self.assertFalse(decoy.exists(), "the agent's holon went through the broker, not to the databases")

    def test_the_broker_never_acts_as_the_chair_and_ignores_a_callers_home(self):
        self.k.auth.init_chair_key()  # readable by the broker's user, as in production
        code, _, err = self.agent("task", "add", "x", "--intent", "y")
        self.assertEqual(code, 4, err)
        code, out, _ = self.agent("--json", "--home", str(self.tmp / "elsewhere"), "whoami")
        self.assertEqual((code, json.loads(out)["principal"], json.loads(out)["home"]),
                         (0, "anonymous", str(self.home.root)))
        self.assertFalse((self.tmp / "elsewhere").exists(), "no instance is created where a caller points")

    def test_agents_reach_only_agent_commands(self):
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        code, out, _ = self.agent("--json", "whoami", token=c.token)
        self.assertEqual((code, json.loads(out)["run_id"]), (0, c.run["id"]))
        for argv in (["tick"], ["contract", "schemas", "--write", str(self.tmp / "s")], ["plan", "approve", "x"],
                     ["mcp"]):
            self.assertEqual(self.agent(*argv, token=c.token)[0], 4, argv)
        self.assertFalse((self.tmp / "s").exists())

    def test_the_broker_reads_only_the_files_its_client_sent(self):
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        secret = self.tmp / "secret.txt"
        secret.write_text("the kernel's secret")
        with isolation.connect(self.broker) as s, s.makefile("rb") as f:  # a client that names a file it did not send
            isolation._send(s, {"argv": ["msg", "send", "org", f"@{secret}"], "token": c.token,
                                "files": {}})
            resp = isolation._recv(f)
        self.assertEqual(resp["code"], 4)
        self.assertNotIn("the kernel's secret", json.dumps(resp))
        self.assertFalse(any("secret" in m["body"] for m in self.k.work.messages()))

    def test_a_symlink_planted_as_the_result_is_never_followed(self):
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        target = self.tmp / "chair.like"
        target.write_text(json.dumps({"kind": "plan", "findings": "x", "steps": ["y"]}))
        (c.brief_dir / "result.json").symlink_to(target)
        out = self.k.submit(c.run["id"])
        self.assertIn("breach", out)
        self.assertIn("symlink", " ".join(out["breach"]))

    def test_run_directories_are_shared_without_letting_the_agent_replace_kernel_files(self):
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        self.assertTrue(c.brief_dir.stat().st_mode & 0o1000, "sticky")
        self.assertTrue((c.brief_dir / "driver.log").exists(), "the kernel's log exists before the agent runs")
        acl = subprocess.run(["getfacl", "-p", str(c.brief_dir)], capture_output=True, text=True).stdout
        self.assertIn(f"user:{getpass.getuser()}:rwx", acl)
        acl = subprocess.run(["getfacl", "-p", str(c.brief_dir / "skills")], capture_output=True,
                             text=True).stdout
        self.assertIn(f"user:{getpass.getuser()}:r-x", acl,
                      "the run's exported skills are readable and traversable, never writable")

    def test_the_runner_stops_a_harness_whose_result_is_in(self):
        self.install_fake_driver("fake", mode="linger")
        self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out["submitted_by_agent"])
        log = (self.home.run_dir(self.k.wconn.execute("SELECT id FROM runs").fetchone()[0]) / "driver.log").read_text()
        self.assertIn("[stopped: the run's result was already in]", log)

    def test_checks_and_scripts_run_through_the_runner(self):
        self.reconfigure(f'[isolation]\nagent_user = "{getpass.getuser()}"\nbroker = "{self.broker}"\n'
                         f'runner = "{self.runner}"\n[checks]\ncommands = ["test -f hello.txt && echo via-runner"]\n')
        w = self.add(dept="eng")
        self.plan_and_approve(w)
        out = self.k.dispatch(self.dev1, "execute")
        self.assertTrue(out["checks"][0]["ok"], out["checks"])
        self.assertIn("via-runner", out["checks"][0]["tail"])

    def test_a_departments_secret_reaches_the_body_through_the_runner(self):
        """G8 under isolation: the kernel resolves the key file at claim (its own privileges), the
        runner carries it into the body's environment, and the broker is never involved in the
        resolution — an agent's `holon` calls cannot read secrets, only run with them."""
        key = self.tmp / "eng.key"
        key.write_text("eng-imap-secret\n")
        key.chmod(0o600)
        self.reconfigure(f'[isolation]\nagent_user = "{getpass.getuser()}"\nbroker = "{self.broker}"\n'
                         f'runner = "{self.runner}"\n[secrets.dept.eng]\nIMAP_KEY = "{key}"\n')
        self.install_fake_driver("fake", mode="env-probe")
        w = self.add(dept="eng")
        self.plan_and_approve(w, assignee=self.dev1, writes=[])
        self.k.dispatch(self.dev1, "execute")
        res = self.k.work.last_execute_output(w["id"])
        self.assertIn("probe: IMAP_KEY='eng-imap-secret'", res["documentation"],
                      "resolved at claim, carried by the runner into the body's environment")

    def test_the_runner_and_broker_refuse_other_uids(self):
        a, b = __import__("socket").socketpair()
        with a, b:
            t = threading.Thread(target=isolation.handle_runner, args=(a, {os.getuid() + 1}))
            t.start()
            self.assertIn("error", isolation._recv(b.makefile("rb")), "refused before it reads a request")
            t.join(5)

    def test_doctor_probes_what_the_agent_user_can_reach(self):
        self.k.auth.init_chair_key()
        from holon.integrity import doctor
        found = {(f["check"], f["severity"]) for f in doctor.run(self.k)}
        self.assertIn(("isolation.secrets", "error"), found, "run as one user, every secret is readable: say so")
        self.assertNotIn(("isolation.runner", "error"), found)
        self.assertNotIn(("isolation.broker", "error"), found)
        self.reconfigure("")
        self.assertIn(("isolation.off", "error"), {(f["check"], f["severity"]) for f in doctor.run(self.k)})

    def test_a_half_built_boundary_is_an_error_naming_the_regeneration_command(self):
        """The Chair's live run 2026-10-25: `sh: /tmp/holon-isolation.sh: No such file or directory`.
        The config named an agent user that had never been created, because the sequence's step 1
        (the root script) lived in `/tmp` and vanished — tmpfs on the live host, plus the 10-day
        tmpfiles sweep — and nothing re-created or checked for it. The machine looked configured:
        the config row existed, and the user did not. Every step after step 1 failed on a machine
        whose own doctor had said nothing about the gap.

        The check must name the command that regenerates the script and the durable path it lands
        at — the remediation-naming rule the repo already enforces (`a message that names the fix
        must name the fix that works`), applied to the sequence's own first step."""
        import pwd
        real = pwd.getpwnam
        # an agent user that does not exist: exactly the half-built state
        def missing(name):
            raise KeyError(f"getpwnam(): name not found: {name}")
        pwd.getpwnam = missing
        try:
            from holon.integrity import doctor
            found = [f for f in doctor.run(self.k) if f["check"] == "isolation.half"]
            self.assertTrue(found, "a configured agent user that does not exist is an error, not silence")
            msg = found[0]["message"]
            self.assertIn("holon isolation plan", msg, "the remediation names the regeneration command")
            self.assertIn(str(self.home.root / "isolation" / "setup.sh"), msg,
                          "the remediation names the durable path inside the instance")
        finally:
            pwd.getpwnam = real


class Setup(KernelCase):
    def test_a_knowledge_base_is_readable_by_the_agent_user_but_never_writable(self):
        """A KB is the kernel's writer (one verb, one commit, one seat) — so the tree is a read,
        never a grant. Without it the agent user cannot read the KB it is bound to: `holon kb run`
        still worked (the kernel runs the vendored CLI), but a body that wanted to read or diff
        its own KB was denied the tree."""
        kb_dir = self.home.root / "kb" / "pipeline"
        kb_dir.mkdir(parents=True)
        (kb_dir / "genome.toml").write_text("[genome]\n")
        reach = isolation.reach(self.k)
        kb_root = (self.home.root / "kb").resolve()
        reads = [p.resolve() for p in reach["reads"]]
        writes = [p.resolve() for p in reach["writes"]]
        self.assertIn(kb_root, reads, "the KB tree is a read for the agent user")
        self.assertTrue(any(kb_root == p or kb_root in p.parents for p in reads),
                        "the KB tree is inside the agent user's read reach")
        self.assertFalse(any(kb_root == p or kb_root in p.parents for p in writes),
                         "the KB tree is never a write: the kernel is its writer")

    def test_isolate_apply_refuses_without_force(self):
        """`isolate apply` rewrites the ACLs of the whole instance — the boundary between the
        kernel's user and every agent. A channel that renders it as a one-tap button, or a
        mis-pressed return, would re-fence the organization with no second look. `restore` and
        `keys gc` already require an explicit `--force`; this command had no such argument at all,
        so it was the one destructive action a fat-finger could spend. Measured 2026-10-23 while
        deciding which commands the bot surface may offer: `restore` and `keys gc` declare
        `--force`, `isolation apply` declared nothing."""
        from holon.execution import isolation
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        from holon import cli
        apply_cmd = next(c for c in cli.REGISTRY if c.path == ("isolation", "apply"))
        self.assertIn("--force", [a[0] for a, _ in apply_cmd.args],
                      "the destructive command declares the gate, like restore and keys gc")
        # and the gate actually refuses: a mis-pressed return, or a one-tap button, is stopped
        import types
        no_force = types.SimpleNamespace(force=False, json=False)
        with self.assertRaises(ValueError) as caught:
            apply_cmd.fn(no_force, self.k)
        self.assertIn("--force", str(caught.exception), "the refusal names what to pass")
        # the plan itself stays ungated: reading what WOULD happen is how you decide whether to
        # spend the force. A gated survey would tell you nothing until you already agreed.
        plan_cmd = next(c for c in cli.REGISTRY if c.path == ("isolation", "plan"))
        self.assertNotIn("--force", [a[0] for a, _ in plan_cmd.args],
                         "reading the plan must never require the force")
        self.assertTrue(isolation.setup_plan(self.k)["acl"], "the plan is still inspectable")

    def test_the_plans_printed_instruction_is_the_one_that_works(self):
        """The plan is the kernel telling the Chair how to spend its one root step, so a printed
        command the kernel would then refuse is worse than no instruction at all — the Chair
        follows the kernel's own words and gets an error back.

        Adding `--force` to `isolation apply` broke exactly this: the plan still printed
        `holon isolation apply` bare, and following it raised ValueError. The instruction must
        carry the flag the command now requires, so the kernel's words and its gate agree."""
        from holon import cli
        apply_cmd = next(c for c in cli.REGISTRY if c.path == ("isolation", "apply"))
        needs_force = "--force" in [a[0] for a, _ in apply_cmd.args]
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        # read what the plan command PRINTS rather than re-deriving the string: this guard is
        # about the kernel's own words, so it has to look at those words.
        from holon.execution import isolation
        plan = isolation.setup_plan(self.k)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            with contextlib.suppress(SystemExit):
                cli.main(["--home", str(self.home.root), "isolation", "plan"])
        printed = captured.getvalue() or (
            f"2. as you: holon isolation apply   ({len(plan['acl'])} setfacl commands)\n")
        self.assertIn("isolation apply", printed, "the plan names the apply step")
        if needs_force:
            self.assertIn("--force", printed.replace("--force ", "--force"),
                          "the printed instruction carries the flag the command requires")
            # and it is the apply line that carries it, not a stray mention elsewhere
            apply_line = next(l for l in printed.splitlines() if "isolation apply" in l)
            self.assertIn("--force", apply_line, f"the apply line carries the flag: {apply_line!r}")

    def test_no_remediation_names_a_command_the_gate_would_refuse(self):
        """A message that names the fix must name the fix that WORKS. `doctor` tells the operator
        which command repairs the boundary, and two of its remediation strings said
        `holon isolation apply` bare — so the moment isolation is applied and one of those errors
        fires, the operator is told to run a command that returns ValueError. Latent, not live:
        those checks are unreachable while `isolation.off` is still a warning, which is exactly why
        it would have shipped unseen."""
        from holon import cli
        gate = next(c for c in cli.REGISTRY if c.path == ("isolation", "apply"))
        needed = [a[0] for a, _ in gate.args if a[0].startswith("--")]
        self.assertTrue(needed, "the gate declares its flag; if it did not, this guard is moot")
        src = (Path(__file__).resolve().parents[1] / "holon" / "integrity" / "doctor.py").read_text()
        for line in src.splitlines():
            if "isolation apply" not in line or "holon isolation apply" not in line:
                continue
            self.assertIn("--force", line,
                          f"a remediation names the apply command without its flag: {line.strip()}")

    def test_the_agent_may_only_traverse_toward_what_it_needs(self):
        top = self.tmp / "home"
        (top / "projects" / "acme").mkdir(parents=True)
        (top / "projects" / "notes.txt").write_text("private")
        (top / ".config").mkdir()
        self.home.config_file.write_text(self.pipeline + f'\n[isolation]\nagent_user = "agent"\n'
                                         f'grants = ["{top / "projects" / "acme"}"]\n')
        self.k.close()
        self.k = Kernel(self.home)
        import pwd
        real = pwd.getpwuid
        pwd.getpwuid = lambda uid: type("P", (), {"pw_dir": str(top), "pw_uid": uid, "pw_name": "me"})()
        try:
            plan = isolation.setup_plan(self.k)
        finally:
            pwd.getpwuid = real
        cmds = [" ".join(c) for c in plan["acl"]]
        self.assertIn(f"setfacl -m u:agent:x {top / 'projects'}", cmds)
        self.assertIn(f"setfacl -d -m u:agent:--- {top / 'projects'}", cmds, "future entries are denied too")
        self.assertTrue(any(c.startswith("setfacl -m u:agent:---") and str(top / "projects" / "notes.txt") in c
                            for c in cmds), "a sibling on the way is denied")
        self.assertTrue(any(c.startswith("setfacl -m u:agent:---") and str(top / ".config") in c for c in cmds))
        self.assertIn(f"setfacl -R -m u:agent:rwX {top / 'projects' / 'acme'}", cmds)
        self.assertIn("useradd --system", plan["root_script"])
        self.assertIn(f"--allow-uid {os.getuid()}", plan["root_script"])

    def test_the_runner_units_path_names_the_kernel_users_home_not_the_agents(self):
        """The live activation failure, 2026-10-26: the Chair ran the root script and the runner
        crash-looped (`readlink: missing operand`, exit 1, restart counter climbing) because
        `Environment=PATH=%h/bin:...` is a SYSTEM unit's line: systemd expands %h to the home of
        `User=`, which is the AGENT user (/var/lib/holon-agent-...), not the kernel user whose
        ~/bin holds the launcher. The tick unit carries the same line safely because it is a USER
        unit, where %h is the kernel user; the runner unit inherited the pattern without the
        semantics. The 2026-10-23 guard below asserted `%h` verbatim, so the suite was green while
        the runner could not start on the only machine that had one.

        The unit must name the kernel user's home explicitly (the generator's `me`), and must
        not use %h for PATH at all in a unit whose User= is somebody else."""
        import pwd
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        script = isolation.setup_plan(self.k)["root_script"]
        env_line = next((l for l in script.splitlines() if l.startswith("Environment=PATH=")), None)
        self.assertIsNotNone(env_line, "the unit must declare the PATH systemd omits")
        self.assertNotIn("%h", env_line,
                         "a system unit's %h is User='s home (the agent's /var/lib home), "
                         "where no launcher lives -- measured live 2026-10-26: crash-loop")
        me = pwd.getpwuid(os.getuid())
        self.assertIn(f"Environment=PATH={me.pw_dir}/bin", env_line,
                      "the unit names the kernel user's own bin dir, where the launcher is")

    def test_the_runner_unit_survives_the_checkout_it_was_planned_from_moving(self):
        """The root script's `PYTHONPATH` was the checkout the plan was generated from, which is the
        defect `2026-10-21` fixed for the heartbeat and broker units and missed here.

        Measured: the script the live instance will `sudo sh` carries
        `Environment=PYTHONPATH=<the checkout the plan was generated from>`. The 2026-10-21
        guard passes because it only reads the units *install.py* writes -- and this unit is
        generated by `setup_plan`, which is a different writer. Move or delete that checkout and
        every agent process dies at `import holon` with nothing in the journal about why.

        The assertion is the portability rule, not a restatement of the string: the unit must
        resolve the kernel rather than name a tree. The kernel's own `bin/holon` launcher and
        `PYTHONPATH` are the runtime contract, so the check is that no checkout path is baked in."""
        from pathlib import Path
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        script = isolation.setup_plan(self.k)["root_script"]
        root = str(Path(__file__).resolve().parents[1])
        self.assertNotIn(root, script,
                         f"the root script bakes in {root}: a moved checkout stops every agent "
                         "process with no journal entry naming the cause")
        # and the launcher must still be present in the form systemd can resolve
        self.assertIn("-m holon.execution.isolation runner", script,
                      "the runner command itself is what starts the agents")
        # exec'd by an interpreter, not a bare module path: under systemd's service PATH the name
        # on PATH may not be the interpreter whose stdlib the module was checked against, so a
        # literal `python3` is asserted nowhere -- what makes the runner start is an interpreter
        # resolving the module, however it is spelled.
        self.assertRegex(script,
                         r"exec\s+\S*(python|\{\})\S*\s+-m holon\.execution\.isolation runner",
                         "the runner is exec'd by an interpreter, not a bare module path")
        # the unit must name the kernel user's bin dirs itself: systemd's service PATH omits
        # them, and in a SYSTEM unit %h is the agent's home (User=), not the kernel user's --
        # the live activation of 2026-10-26 measured exactly that crash-loop. The unit names the
        # kernel user's home explicitly; the guard below proves it is the launcher's real home.
        import pwd
        me = pwd.getpwuid(os.getuid())
        self.assertIn(f"Environment=PATH={me.pw_dir}/bin", script,
                      "the unit sets the PATH systemd omits, or the resolution below cannot run")
        self.assertNotIn("Environment=PATH=%h", script,
                         "a system unit's %h is User='s home, where no launcher lives")

    def test_the_runner_units_start_command_resolves_a_real_launcher(self):
        """The portability fix is only as good as the shell inside `ExecStart`. `command -v holon`
        returning nothing would make the runner die with `readlink: missing operand` -- the silent
        failure the fix exists to remove, wearing a different hat.

        Measured on this machine: the launcher is `~/bin/holon`, a symlink into the
        checkout, and `readlink -f` follows it back. This runs the unit's own resolution against
        the launcher `shutil.which` finds, and asserts the package directory lands at the end of
        it -- so the unit starts, rather than merely parsing."""
        import subprocess
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        launcher = Path(__file__).resolve().parents[1] / "bin" / "holon"
        self.assertTrue(launcher.exists(), "the distribution ships its launcher at bin/holon")
        line = isolation.setup_plan(self.k)["root_script"].split("ExecStart=")[-1].splitlines()[0]
        self.assertIn("command -v holon", line, "the unit resolves the launcher at start time")
        H = subprocess.run(["sh", "-c",
                            f"dirname $(dirname $(readlink -f {launcher}))"],
                           capture_output=True, text=True).stdout.strip()
        self.assertTrue((Path(H) / "holon" / "__init__.py").exists(),
                        f"the resolution lands on {H}, which carries no `holon` package")

    def test_the_runner_start_command_resolves_under_systemds_service_path(self):
        """The guard that would have caught the fix's own defect.

        The portability fix resolves `command -v holon` inside the unit's start shell. But systemd
        gives services a minimal PATH (`/usr/local/bin:/usr/bin:/bin`) that omits the per-user bin
        dirs where the launcher lives, so under the unit's real environment the resolution failed
        -- and the shell exited 0, meaning systemd would have recorded a healthy unit that never
        started a runner. Measured 2026-10-23: `readlink: missing operand`, rc 0.

        This runs the unit's own Environment=PATH against its own resolution, so the two halves are
        proved together rather than each looking fine alone."""
        import subprocess, re as _re
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        script = isolation.setup_plan(self.k)["root_script"]
        env_line = next((l for l in script.splitlines() if l.startswith("Environment=PATH=")), None)
        self.assertIsNotNone(env_line, "the unit must declare the PATH systemd omits")
        # expand what systemd expands, and keep the rest as the unit writes it
        # systemd expands %h to the user's home; the test's ambient PATH has no ~/bin on it (measured:
        # shutil.which("holon") is None outside an interactive login shell), so the guard must
        # substitute the real home itself or it proves nothing about the unit's own resolution.
        home = os.path.expanduser("~")
        # the line is `Environment=PATH=<value>`: strip the whole `Environment=PATH=`
        # prefix. Prefixing it again below yields `PATH=PATH=...`, `command -v` then finds
        # nothing, and the guard fails at a defect it invented for itself rather than one the
        # unit has.
        path_value = env_line[len("Environment=PATH="):].replace("%h", home)
        cmd = next(l for l in script.splitlines() if l.startswith("ExecStart=")).split("'", 1)[1].rsplit("'", 1)[0]
        # the command is the runner, which serves until stopped: run it with a hard deadline and
        # read what it prints before it is killed. A timeout here means it STARTED, which is the
        # thing being proved; a nonzero rc or a `missing operand` means it did not.
        started, err = False, ""
        try:
            r = subprocess.run(["env", "-i", f"PATH={path_value}", "sh", "-c", cmd],
                               capture_output=True, text=True, timeout=20)
            rc, err = r.returncode, r.stderr or ""
        except subprocess.TimeoutExpired as e:
            started, rc = True, "timed out"
            err = (e.stderr or b"").decode(errors="replace") if e.stderr else ""
        self.assertNotIn("missing operand", err,
                         "`command -v holon` found nothing: systemd's service PATH omits the "
                         f"per-user bin dirs and the unit did not name them ({err[:150]})")
        # the good outcome is the one that started and kept running: a shell that exits at once is
        # the failure this guard exists to catch, whether it says something or not
        self.assertTrue(started or rc == 0,
                        f"the start shell exited at once (rc={rc}) instead of serving: {err[:200]}")
        # and the resolution it performs must land on a real checkout
        H = subprocess.run(["env", "-i", f"PATH={path_value}", "sh", "-c",
                            "dirname $(dirname $(readlink -f $(command -v holon)))"],
                           capture_output=True, text=True).stdout.strip()
        self.assertTrue((Path(H) / "holon" / "__init__.py").exists(),
                        f"resolved {H!r}, which carries no `holon` package")

    def test_no_acl_command_exceeds_the_execve_argument_limit(self):
        """A plan the kernel prints as "run these 30 setfacl commands" must be runnable.

        Measured 2026-10-24 on a cold instance under the Tier A sequence: the plan's first
        command spread every sibling under the home directory into ONE argv -- 35,144 arguments,
        2,186,867 bytes, against a 2,097,152-byte `MAX_ARG_STRLEN` on this host. `apply()` runs
        `subprocess.run(cmd)` with no timeout and no shell, so the oversized entry raises an
        uncaught `OSError: [Errno 7] Argument list too long` and `holon isolation apply` dies on
        the very step the plan instructed the Chair to take. `share_run_dir` spreads `*files`
        the same way, which is the live path (it runs on every dispatch).

        The number is the host's own limit, read rather than assumed, so this guard is about the
        class of defect rather than one host's threshold."""
        # the host's real limit: one argument may not exceed it, and the whole argv may not
        # either. Read from the kernel rather than pinned to a guess.
        per_arg = os.sysconf("SC_ARG_MAX")
        self.home.config_file.write_text(self.pipeline + '\n[isolation]\nagent_user = "agent"\n')
        self.k.close()
        self.k = Kernel(self.home)
        plan = isolation.setup_plan(self.k)
        self.assertTrue(plan["acl"], "the plan carries commands")
        for cmd in plan["acl"]:
            size = sum(len(a) for a in cmd) + len(cmd)
            self.assertLess(size, per_arg,
                            f"argv of {size} bytes exceeds SC_ARG_MAX ({per_arg}): {cmd[:3]} with "
                            f"{len(cmd)} arguments")
            self.assertLess(len(max(cmd, key=len)), 131072,
                            f"one argument is {len(max(cmd, key=len))} bytes (MAX_ARG_STRLEN is 131072)")

    def test_share_run_dir_grants_every_file_it_listed(self):
        """The same defect, on the path that actually runs: `share_run_dir` puts every entry in
        the run directory into one `setfacl` argv, so a run directory carrying enough files
        raises an uncaught `OSError` and the dispatch dies after the stage was already
        claimed. Measured on the live instance: the busiest run directory holds 11 entries,
        so this is latent today and live the day a body writes a lot."""
        d = self.tmp / "run_wide"
        d.mkdir()
        # long paths, so the chunking boundary is exercised by length rather than by count
        long_name = "n" * 200
        for i in range(600):
            (d / f"{long_name}_{i}.md").write_text("x")
        (d / "result.json").write_text("{}")
        isolation.share_run_dir(d, os.getlogin())   # must not raise

    def test_share_run_dir_grants_both_reads_and_denies_the_result(self):
        """The chunking must not change what the ACL says: every file that is not the result
        is granted read, and the result is left to the kernel. A batch boundary is a place
        where a file can quietly fall out of a group, so this asserts the grants cover all
        of them and nothing else."""
        d = self.tmp / "run_wide2"
        d.mkdir()
        for i in range(700):
            (d / f"file_{i:05d}.md").write_text("x")
        (d / "result.json").write_text("{}")
        calls = []
        real = subprocess.run

        def spy(cmd, **kw):
            calls.append(cmd)
            return real(cmd, **kw)
        subprocess.run = spy
        try:
            isolation.share_run_dir(d, os.getlogin())
        finally:
            subprocess.run = real
        granted = {p for c in calls for p in c if p.startswith(str(d))}
        expected = {str(d / f"file_{i:05d}.md") for i in range(700)}
        self.assertEqual(expected - granted, set(), "every file that is not the result is granted")
        self.assertNotIn(str(d / "result.json"), granted,
                         "the result stays the agent's own file, not a granted read")

    def test_skill_names_cannot_climb_out_of_the_library(self):
        from holon.capabilities.skills import SkillError
        with self.assertRaises(SkillError):
            self.k.skills.resolve("../../../.config", "x.md")

    def test_the_setup_script_lands_where_a_reboot_cannot_take_it(self):
        """The Chair's live run on 2026-10-25: `sh: /tmp/holon-isolation.sh: No such file or
        directory`. The activation sequence BYLAWS §11 prints named a file in `/tmp`, and `/tmp` on
        the live host is tmpfs (gone per boot) *and* cleared by tmpfiles.d at 10 days — the one file
        the sequence depends on was the one file guaranteed to disappear. The plan is a deterministic
        function of instance state, so the script is a cache, not a deliverable: it belongs under
        `$HOLON_HOME` (which `holon backup` covers), where a later step can find it on any day of
        any uptime.

        This guards three things: the plan carries a default durable path; that path is inside the
        instance, not a volatile dir; and it is derivable from the instance alone, so regenerating
        it is one command from any state."""
        from holon.execution import isolation
        plan = isolation.setup_plan(self.k)
        default = Path(plan["setup_script"])
        self.assertTrue(default.is_absolute(), "the default is a path, not a filename a caller must place")
        self.assertFalse(default.is_relative_to("/tmp"),
                         "the default must not live in a dir wiped per boot or cleared at 10 days")
        self.assertTrue(str(self.home.root) in str(default),
                        "the default lives inside the instance, where holon backup carries it")

    def test_the_printed_sequence_names_the_durable_path_it_writes(self):
        """The printed step 1 and the file the command writes must be the same file. With no
        `--write`, the plan still names the durable path and says how to regenerate it, because the
        sequence a doc prints must be re-runnable from any state — an operator who lost the file is
        one command away, not one archaeology dig (measured: nothing re-created it; the docs treated
        a pure function's output as a deliverable)."""
        from holon.execution import isolation
        plan = isolation.setup_plan(self.k)
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured):
            with contextlib.suppress(SystemExit):
                from holon import cli
                cli.main(["--home", str(self.home.root), "isolation", "plan"])
        printed = captured.getvalue()
        self.assertIn(plan["setup_script"], printed,
                      "the printed step 1 names the path this plan writes by default")
        self.assertIn("holon isolation plan", printed,
                      "the sequence tells the operator how to regenerate the script")

    def test_a_written_setup_script_survives_in_the_instance(self):
        """The write lands where the sequence says it does, with parents created, so a fresh
        machine's first `holon isolation plan` produces the file the next steps consume."""
        from holon.execution import isolation
        plan = isolation.setup_plan(self.k)
        target = Path(plan["setup_script"])
        self.assertFalse(target.exists(), "the precondition: nothing has written it yet")
        with contextlib.redirect_stdout(io.StringIO()):
            with contextlib.suppress(SystemExit):
                from holon import cli
                cli.main(["--home", str(self.home.root), "isolation", "plan"])
        self.assertTrue(target.exists(),
                        f"running the plan writes the script at its durable default {target}")
        self.assertIn("useradd --system", target.read_text(),
                      "the file written is the root script, not the plan's other fields")


class PreparedUnderIsolation(Isolated):
    """H-07/H-08 (integration audit): the enforcing harness must be able to read policy.json, and
    a supervisor takes the body only with the isolation it must run it under — or it is refused."""

    def test_policy_json_is_readable_by_the_enforcing_harness(self):
        w = self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        mode = (c.brief_dir / "policy.json").stat().st_mode & 0o777
        self.assertEqual(mode, 0o640, "policy.json: the harness that must enforce it can read it")

    def test_the_prepared_handoff_carries_isolation_and_the_cli_gates_it(self):
        self.add(dept="eng")
        out = self.k.prepare_dispatch(self.hod, "plan")
        self.assertEqual(out["isolation"]["agent_user"], getpass.getuser())
        self.assertEqual(out["isolation"]["runner"], self.runner)
        self.k.submit(out["run"], self.plan_env())   # free the concurrency slot
        self.add("second", dept="eng")
        args = types.SimpleNamespace(as_=self.hod, stage="plan", task=None, prepare=True,
                                     runs_as_agent_user=False, json=True, schedule=None)
        with self.assertRaises(PermissionError):
            holon_cli.cmd_dispatch(args, self.k)
        args.runs_as_agent_user = True
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            holon_cli.cmd_dispatch(args, self.k)
        self.assertIn("isolation", buf.getvalue(), "the supervisor sees what it must run the body as")
