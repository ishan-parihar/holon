"""R3: who may act. The Chair key, employee keys, run tokens, and the command registry's actor classes."""

import io
import json
import os
import stat
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout

from holon import cli
from tests.helpers import KernelCase


class Security(KernelCase):
    def cli(self, *argv, **env):
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update({k: v for k, v in env.items() if v is not None})
        for k, v in env.items():
            if v is None:
                os.environ.pop(k, None)
        out, err = io.StringIO(), io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(err):
                rc = cli.main(["--home", str(self.home.root), "--json", *argv])
        finally:
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        text = out.getvalue()
        try:
            data = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            data = text
        return rc, data, err.getvalue()

    def test_open_mode_until_init_then_the_chair_key_is_required(self):
        rc, _, err = self.cli("task", "add", "x", "--intent", "y", "--dept", "eng")
        self.assertEqual(rc, 0)
        self.assertIn("open mode", err)
        rc, data, _ = self.cli("init")
        key = self.tmp / "chair.key"
        self.assertEqual(data["chair_key"], str(key))
        self.assertEqual(stat.S_IMODE(key.stat().st_mode), 0o600)
        self.assertEqual(self.cli("task", "add", "x2", "--intent", "y", "--dept", "eng")[0], 0)
        nobody = {"HOLON_CHAIR_KEY_FILE": str(self.tmp / "missing.key")}
        self.assertEqual(self.cli("task", "add", "x3", "--intent", "y", **nobody)[0], 4)
        self.assertEqual(self.cli("task", "list", **nobody)[0], 0, "reading needs no key")
        self.assertEqual(self.cli("init", "--rotate-chair-key", **nobody)[0], 4)
        self.assertEqual(self.cli("plan", "approve", "tsk_x", **{"HOLON_CHAIR_KEY": "hck_wrong", **nobody})[0], 4)

    def test_employee_key_scopes_what_an_employee_may_do(self):
        self.cli("init")
        _, data, _ = self.cli("hr", "key", self.hod)
        as_bo = {"HOLON_KEY": data["key"], "HOLON_CHAIR_KEY_FILE": str(self.tmp / "missing.key")}
        self.add(dept="eng")
        rc, claim, _ = self.cli("claim", "--stage", "plan", **as_bo)
        self.assertEqual(rc, 0)
        self.assertEqual(self.cli("claim", "--as", self.dev1, **as_bo)[0], 4, "no acting as a colleague")
        self.assertEqual(self.cli("plan", "approve", "tsk_x", **as_bo)[0], 4)
        self.assertEqual(self.cli("charter", "authority", "--set-for", f"emp:{self.hod}", "--max-grade", "G4",
                                  **as_bo)[0], 4, "no widening your own authority")
        self.assertEqual(self.cli("memory", "add", "--scope", "org", "--kind", "fact", "--content", "c", **as_bo)[0], 4)
        self.assertEqual(self.cli("memory", "add", "--scope", "dept:eng", "--kind", "fact", "--content", "c",
                                  **as_bo)[0], 0)
        self.cli("hr", "key", self.hod, "--revoke")
        self.assertEqual(self.cli("claim", **as_bo)[0], 4, "a revoked key stops working")

    def test_shared_scopes_are_fenced_for_employees(self):
        """R-0: an employee contributes non-steering knowledge to a class scope their department
        performs, and to project scopes of their own department's work; steering kinds in shared
        scopes are consolidation-or-Chair-only; reads stay open."""
        self.cli("init")
        _, data, _ = self.cli("hr", "key", self.hod)
        as_bo = {"HOLON_KEY": data["key"], "HOLON_CHAIR_KEY_FILE": str(self.tmp / "missing.key")}
        t = self.add(dept="eng", task_class="ops")
        # class: the department performs -> allowed for a non-steering kind
        self.assertEqual(self.cli("memory", "add", "--scope", "class:ops", "--kind", "fact", "--content",
                                  "the atlas route needs a token", **as_bo)[0], 0)
        # steering into a shared scope -> refused on every path
        self.assertEqual(self.cli("memory", "add", "--scope", "class:ops", "--kind", "constraint", "--content",
                                  "c", **as_bo)[0], 4, "steering kinds are consolidation-or-Chair-only")
        self.assertEqual(self.cli("memory", "add", "--scope", "class:ops", "--kind", "decision", "--content",
                                  "c", **as_bo)[0], 4)
        # a class the department does not perform -> refused even for a fact
        self.assertEqual(self.cli("memory", "add", "--scope", "class:nothing", "--kind", "fact", "--content",
                                  "c", **as_bo)[0], 4)
        # the department's own project scope -> allowed; a foreign project's -> refused
        self.k.wconn.execute("UPDATE tasks SET project_ref='proj:mine' WHERE id=?", (t["id"],))
        self.k.wconn.commit()
        self.assertEqual(self.cli("memory", "add", "--scope", "proj:mine", "--kind", "fact", "--content",
                                  "c", **as_bo)[0], 0)
        self.assertEqual(self.cli("memory", "add", "--scope", "proj:foreign", "--kind", "fact", "--content",
                                  "c", **as_bo)[0], 4)
        # reads stay open: the employee searches every scope without a fence
        self.assertEqual(self.cli("memory", "search", "atlas", **as_bo)[0], 0)

    def test_run_token_authenticates_one_run(self):
        self.cli("init")
        t = self.add(dept="eng")
        _, mine, _ = self.cli("claim", "--as", self.hod, "--stage", "plan")
        (self.home.run_dir(mine["run_id"]) / "result.json").write_text(json.dumps(self.plan_env()))
        other = self.k.claim("plan", self.hod, task_id=self.add("second", dept="eng")["id"])
        tok = {"HOLON_RUN_TOKEN": mine["token"], "HOLON_CHAIR_KEY_FILE": str(self.tmp / "missing.key")}
        self.assertEqual(self.cli("submit", other.run["id"], **tok)[0], 4, "a token covers only its own run")
        self.assertEqual(self.cli("run", "heartbeat", mine["run_id"], **tok)[0], 0)
        self.assertEqual(self.cli("submit", mine["run_id"], **tok)[0], 0)
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))
        self.assertEqual(self.cli("submit", mine["run_id"], **tok)[0], 4, "the token dies with its run")
        brief = self.home.run_dir(mine["run_id"]) / "brief.json"
        self.assertEqual(stat.S_IMODE(brief.stat().st_mode), 0o600)

    def test_the_chair_key_never_reaches_a_harness(self):
        os.environ["HOLON_CHAIR_KEY"] = "hck_secret"
        try:
            self.add(dept="eng")
            out = self.k.dispatch(self.hod, "plan")  # fake_agent asserts the key is absent
        finally:
            os.environ.pop("HOLON_CHAIR_KEY")
        self.assertIn("approval", out)

    def test_every_command_declares_an_actor_class(self):
        rc, manifest, _ = self.cli("help")
        self.assertEqual(rc, 0)
        actors = {c["command"]: c["actor"] for c in manifest}
        # `task add` is deliberately reachable by an intake principal as well as the Chair: a
        # scanner files its own lead with an intake key and no Chair key (G4). Every other
        # Chair-only command is unchanged.
        self.assertEqual(actors["task add"], "intake")
        for cmd in ("plan approve", "hr hire", "hr key", "dept profile", "audit judge"):
            self.assertEqual(actors[cmd], "chair", cmd)
        for cmd in ("claim", "submit", "run heartbeat", "msg send"):
            self.assertEqual(actors[cmd], "employee", cmd)
        self.assertEqual(actors["tick"], "kernel")


class KeyCollection(KernelCase):
    """B-2: a scratch instance's Chair key outlives it, and there was no way to collect it —
    `holon keys list --orphans` named them and left the deletion to hand. The sweep collects them,
    quarantining rather than unlinking (a wrong "live" is the only signal error there is, and it
    would lock the Chair out of a running instance), and it never reaches the key of the instance
    it runs in, nor anything the operator pinned with --keep."""

    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli.main(["--home", str(self.home.root), "--json", *argv])
        text = out.getvalue()
        try:
            data = json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            data = text
        return rc, data, err.getvalue()

    def setUp(self):
        super().setUp()
        # The instance's own key is <instance>.chair in the real world (the kernel writes it 0600
        # into a 0700 dir); the test harness pins it to a bare name beside the scratch home. Write
        # it the real way — through the kernel's own writer, which is what the mode assertion below
        # is about — and point the env var at it, so the sweep sees exactly the real shape.
        self.mine = self.k.auth.instance_id + ".chair"
        os.environ["HOLON_CHAIR_KEY_FILE"] = str(self.tmp / self.mine)
        fd = os.open(self.tmp / self.mine, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write("hck_mine\n")

    def _sweep(self, *argv):
        return self.cli("keys", "gc", *argv)

    def _seed(self, *names):
        for n in names:
            (self.tmp / f"{n}.chair").write_text("hck_x\n")

    def test_a_dry_run_reports_and_collects_nothing(self):
        self._seed("orphan1", "orphan2")
        rc, out, _ = self._sweep()
        self.assertEqual(rc, 0)
        self.assertEqual(sorted(r["file"] for r in out["orphans"]), ["orphan1.chair", "orphan2.chair"])
        self.assertEqual([r["file"] for r in out["kept"]], [self.mine],
                         "the running instance's own key is never a candidate")
        self.assertEqual(len(list(self.tmp.glob("*.chair"))), 3, "nothing moved on a dry run")

    def test_the_running_instances_own_key_is_never_collected(self):
        self._seed("orphan1")
        rc, out, _ = self._sweep("--apply")
        self.assertEqual(rc, 0)
        self.assertIn(self.mine, out["kept"])
        self.assertNotIn(self.mine, out["removed"])
        self.assertTrue((self.tmp / self.mine).exists(), "the key of the instance you run in survives")
        self.assertFalse((self.tmp / "orphan1.chair").exists(), "the orphan is collected")

    def test_a_collected_key_is_quarantined_not_unlinked(self):
        self._seed("orphan1")
        rc, out, _ = self._sweep("--apply")
        q = Path(out["quarantine"])
        self.assertTrue((q / "orphan1.chair").exists(),
                        "a wrong 'live' locks the Chair out of a running instance; a mv recovers it")
        self.assertEqual(stat.S_IMODE(q.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((q / "orphan1.chair").stat().st_mode), 0o600)
        self.assertEqual(q.resolve().parent, self.tmp.resolve(), "the quarantine sits beside the key dir")

    def test_a_kept_key_is_left_where_it_is(self):
        self._seed("live", "gone")
        rc, out, _ = self._sweep("--apply", "--keep", "live.chair")
        self.assertTrue((self.tmp / "live.chair").exists(), "--keep is the Chair's veto")
        self.assertFalse((self.tmp / "gone.chair").exists())
        self.assertEqual(out["removed"], ["gone.chair"])
        self.assertIn("live.chair", out["kept"])

    def test_the_operator_key_dir_is_never_the_test_sweeps_target(self):
        """The guard on the guard: the sweep reads the dir the instance's own key lives in (which
        HOLON_CHAIR_KEY_FILE redirects), never the operator's real one."""
        real = Path("~/.config/holon/keys").expanduser().resolve()
        mine = Path(os.environ["HOLON_CHAIR_KEY_FILE"]).resolve().parent
        self.assertNotEqual(mine, real)
        before = sorted(p.name for p in real.glob("*.chair"))
        self._seed("orphan1")
        rc, out, _ = self._sweep("--apply")
        self.assertEqual(Path(out["dir"]).resolve(), mine)
        self.assertEqual(sorted(p.name for p in real.glob("*.chair")), before,
                         "the real key dir is untouched by a test's sweep")

    def test_the_key_dir_mode_is_reported(self):
        """The survey claimed '250 world-readable keys' and was wrong: the dir was 0700 and each
        file 0600. The sweep reports the mode so that claim is checkable, not asserted."""
        rc, out, _ = self._sweep()
        self.assertEqual(out["dir_mode"], oct(self.tmp.stat().st_mode & 0o777)[2:],
                         "the reported mode is the real one, not an assertion")
        self.assertEqual(stat.S_IMODE((self.tmp / self.mine).stat().st_mode), 0o600,
                         "a key file is never world-readable")
