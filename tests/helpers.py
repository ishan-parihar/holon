import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from holon.core.paths import Home
from holon.execution.service import Kernel

FAKE_AGENT = Path(__file__).with_name("fake_agent.py")


class KernelCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="holon-test-"))
        self.home = Home(self.tmp / "home")
        self.workdir = self.tmp / "work"
        self.workdir.mkdir()
        self.k = Kernel(self.home, actor="human:test")
        self.install_fake_driver("fake", "file")

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
               "learnings": ["greeting file is hello.txt"], "next_intent": "next thing"}
        env.update(over)
        return env

    def write_result(self, run_id, env):
        (self.home.run_dir(run_id) / "result.json").write_text(json.dumps(env))
