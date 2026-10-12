import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from holon.core.paths import Home
from holon.execution.service import Kernel

FAKE_AGENT = Path(__file__).with_name("fake_agent.py")
JD = "Responsibilities: do the work well. KPIs: verified results."


class KernelCase(unittest.TestCase):
    """A fresh organization per test:

        executive: CEO (plan, review)
        eng:       HOD (plan, review, verify) · dev1 (execute) · dev2 (execute, verify)
    """
    config = ""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="holon-test-"))
        self.home = Home(self.tmp / "home")
        self.home.root.mkdir(parents=True)
        if self.config:
            self.home.config_file.write_text(self.config)
        self.workdir = self.tmp / "work"
        self.workdir.mkdir()
        self.k = Kernel(self.home)
        self.install_fake_driver("fake")
        o = self.k.org
        self.ceo = o.hire(name="Ada", title="CEO", dept="executive", job_description=JD, duties=["plan", "review"],
                          driver="fake", reason="founding CEO", head=True, status="active")["id"]
        o.create_dept("eng", "Engineering", "Build and fix things")
        self.hod = o.hire(name="Bo", title="Head of Engineering", dept="eng", job_description=JD,
                          duties=["plan", "review", "verify"], driver="fake", reason="founding HOD", head=True,
                          status="active")["id"]
        self.dev1 = o.hire(name="Cy", title="Engineer", dept="eng", job_description=JD, duties=["execute"],
                           driver="fake", reason="capacity", status="active")["id"]
        self.dev2 = o.hire(name="Di", title="Engineer", dept="eng", job_description=JD, duties=["execute", "verify"],
                           driver="fake", reason="capacity", status="active")["id"]

    def tearDown(self):
        self.k.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install_fake_driver(self, name, result="file", mode="ok"):
        self.home.drivers_dir.mkdir(parents=True, exist_ok=True)
        (self.home.drivers_dir / f"{name}.toml").write_text(
            f'name = "{name}"\ncommand = "{sys.executable} {FAKE_AGENT}"\ntimeout_s = 60\n'
            f'result = "{result}"\n[env]\nFAKE_MODE = "{mode}"\n')

    def add(self, title="Write hello", **kw):
        kw.setdefault("workdir", str(self.workdir))
        kw.setdefault("acceptance", "hello.txt exists")
        return self.k.work.add_task(title, kw.pop("intent", "Create hello.txt"), **kw)

    def plan_env(self, **over):
        env = {"kind": "plan", "findings": "empty dir", "steps": ["write hello.txt"], "writes": ["hello.txt"]}
        env.update(over)
        return env

    def exec_env(self, **over):
        env = {"outcome": "success", "summary": "wrote it", "documentation": "# Result\n\nwrote hello.txt",
               "learnings": ["greeting files live at the workdir root"], "next_intent": "next thing"}
        env.update(over)
        return env

    def plan_and_approve(self, task, planner=None, **plan_over):
        c = self.k.claim("plan", planner or self.hod, task_id=task["id"])
        plan = self.k.submit(c.run["id"], self.plan_env(**plan_over))["plan"]
        self.k.approve(plan["id"])
        return plan

    def write_result(self, run_id, env):
        (self.home.run_dir(run_id) / "result.json").write_text(json.dumps(env))
