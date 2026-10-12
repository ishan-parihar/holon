"""R3: who may act. The Chair key, employee keys, run tokens, and the command registry's actor classes."""

import io
import json
import os
import stat
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
        for cmd in ("plan approve", "task add", "hr hire", "hr key", "dept profile", "audit judge"):
            self.assertEqual(actors[cmd], "chair", cmd)
        for cmd in ("claim", "submit", "run heartbeat", "msg send"):
            self.assertEqual(actors[cmd], "employee", cmd)
        self.assertEqual(actors["tick"], "kernel")
