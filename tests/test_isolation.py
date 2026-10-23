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
        self.assertIn(("isolation.off", "warning"), {(f["check"], f["severity"]) for f in doctor.run(self.k)})


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

    def test_skill_names_cannot_climb_out_of_the_library(self):
        from holon.capabilities.skills import SkillError
        with self.assertRaises(SkillError):
            self.k.skills.resolve("../../../.config", "x.md")


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
