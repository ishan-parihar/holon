import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from holon.core.paths import Home
from holon.execution.service import Kernel

FAKE_AGENT = Path(__file__).with_name("fake_agent.py")
REPO = Path(__file__).resolve().parents[1]
JD = "Responsibilities: do the work well. KPIs: verified results."
# the minimal pipeline (plan -> approve -> execute -> verify) unless a test turns stages on; no doctor on
# the heartbeat (test instances run in open mode, which doctor reports) unless a test turns it on
MINIMAL = '[pipeline]\nresearch = "never"\nshape = "never"\nretro = "never"\n[healing]\ndoctor_every_min = 0\n'
ENV_KEYS = ("HOLON_CHAIR_KEY_FILE", "HOLON_CHAIR_KEY", "HOLON_KEY", "HOLON_RUN_TOKEN")


class KernelCase(unittest.TestCase):
    """A fresh organization per test:

        executive: Ada, CEO (head, planner)
        eng:       Bo, head (head, planner, verifier) · Cy, dev1 (executor) · Di, dev2 (executor, verifier)
    """
    config = ""
    pipeline = MINIMAL

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="holon-test-"))
        self.home = Home(self.tmp / "home")
        self.home.root.mkdir(parents=True)
        self.home.config_file.write_text(self.pipeline + "\n" + self.config)
        self.workdir = self.tmp / "work"
        self.workdir.mkdir()
        self._env = {k: os.environ.get(k) for k in ENV_KEYS}
        for k in ENV_KEYS:
            os.environ.pop(k, None)
        os.environ["HOLON_CHAIR_KEY_FILE"] = str(self.tmp / "chair.key")
        self.k = Kernel(self.home)
        self.install_fake_driver("fake")
        o = self.k.org
        self.ceo = o.hire(name="Ada", title="CEO", dept="executive", job_description=JD, slot="ceo",
                          driver="fake", reason="founding CEO", status="active")["id"]
        o.create_dept("eng", "Engineering", "Build and fix things")
        self.hod = o.hire(name="Bo", title="Head of Engineering", dept="eng", job_description=JD,
                          roles=["head", "planner", "verifier"], driver="fake", reason="founding HOD",
                          status="active")["id"]
        self.dev1 = o.hire(name="Cy", title="Engineer", dept="eng", job_description=JD, roles=["executor"],
                           driver="fake", reason="capacity", status="active")["id"]
        self.dev2 = o.hire(name="Di", title="Engineer", dept="eng", job_description=JD,
                           roles=["executor", "verifier"], driver="fake", reason="capacity", status="active")["id"]

    def tearDown(self):
        self.k.close()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_fake_driver(self, name, result="file", mode="ok", extra=""):
        self.home.drivers_dir.mkdir(parents=True, exist_ok=True)
        (self.home.drivers_dir / f"{name}.toml").write_text(
            f'name = "{name}"\ncommand = "{sys.executable} {FAKE_AGENT}"\ntimeout_s = 60\n'
            f'result = "{result}"\n{extra}\n[env]\nFAKE_MODE = "{mode}"\nPYTHONPATH = "{REPO}"\n')

    def add(self, title="Write hello", **kw):
        kw.setdefault("workdir", str(self.workdir))
        kw.setdefault("acceptance", "hello.txt exists")
        return self.k.work.add_work(title, kw.pop("intent", "Create hello.txt"), **kw)

    def plan_env(self, **over):
        env = {"kind": "plan", "findings": "empty dir", "steps": ["write hello.txt"], "writes": ["hello.txt"]}
        env.update(over)
        return env

    def exec_env(self, **over):
        env = {"outcome": "success", "summary": "wrote it", "documentation": "# Result\n\nwrote hello.txt",
               "learnings": ["greeting files live at the workdir root"], "next_intent": "next thing"}
        env.update(over)
        return env

    def plan(self, work, planner=None, **plan_over):
        """Plan `work`; returns the approve stage it was routed to."""
        c = self.k.claim("plan", planner or self.hod, task_id=work["id"])
        return self.k.submit(c.run["id"], self.plan_env(**plan_over))["approval"]

    def plan_and_approve(self, work, planner=None, **plan_over):
        approval = self.plan(work, planner, **plan_over)
        self.k.approve(approval["id"])
        return approval

    def status(self, work):
        return self.k.work.get(work["id"] if isinstance(work, dict) else work)["status"]

    def pipeline_of(self, work):
        return [(s["stage"], s["status"]) for s in self.k.work.stages(work["id"])]

    def write_result(self, run_id, env):
        (self.home.run_dir(run_id) / "result.json").write_text(json.dumps(env))
