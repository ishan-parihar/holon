"""Machine checks ([checks]): acceptance a command can decide, run by the kernel after execute."""

from holon.execution.service import Kernel
from tests.helpers import KernelCase


class Checks(KernelCase):
    def configure(self, toml):
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + toml)
        self.k.close()
        self.k = Kernel(self.home)

    def execute(self, write: bool):
        work = self.add(dept="eng", task_class="greeting")
        self.plan_and_approve(work)
        c = self.k.claim("execute", self.dev1)
        if write:
            (self.workdir / "hello.txt").write_text("hi\n")
        return work, self.k.submit(c.run["id"], self.exec_env())

    def test_a_failing_check_is_rework_without_spending_a_verifier(self):
        self.configure('[checks.dept.eng]\ncommands = ["test -f hello.txt"]\n')
        work, out = self.execute(write=False)
        self.assertFalse(out["checks"][0]["ok"])
        stages = self.pipeline_of(work)
        self.assertIn(("verify", "done"), stages)
        self.assertEqual(stages[-1], ("execute", "ready"), "the executor gets another attempt")
        c = self.k.claim("execute", self.dev1)
        self.assertIn("Machine checks failed", (c.brief_dir / "context.md").read_text())

    def test_passing_checks_reach_the_verifier(self):
        self.configure('[checks]\ncommands = ["true"]\n[checks.class.greeting]\ncommands = ["grep -q hi hello.txt"]\n')
        work, out = self.execute(write=True)
        self.assertEqual([c["command"] for c in out["checks"]], ["true", "grep -q hi hello.txt"])
        self.assertEqual(self.pipeline_of(work)[-1], ("verify", "ready"))
        c = self.k.claim("verify", self.hod)
        self.assertIn("Machine checks after execution", (c.brief_dir / "context.md").read_text())

    def test_no_checks_configured_changes_nothing(self):
        work, out = self.execute(write=True)
        self.assertEqual(out["checks"], [])
        self.assertEqual(self.pipeline_of(work)[-1], ("verify", "ready"))


class Taxonomy(KernelCase):
    def configure(self, toml):
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + toml)
        self.k.close()
        self.k = Kernel(self.home)

    def test_agents_use_the_departments_task_classes(self):
        from holon.integrity import doctor
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        w = self.add(dept="eng", task_class="greeting")
        c = self.k.claim("plan", self.hod)
        self.assertIn("`greeting`, `docs`", (c.brief_dir / "context.md").read_text().replace("`incident`, ", ""))
        out = self.k.submit(c.run["id"], {"kind": "decompose", "rationale": "two parts", "children": [
            {"title": "a", "intent": "a", "task_class": "greeting"},
            {"title": "b", "intent": "b", "task_class": "poetry"}]})
        self.assertIn("'poetry' is not one of eng's", out["breach"][0])
        self.add("odd one", dept="eng", task_class="poetry")  # the Chair may still file anything
        self.assertIn("classes.unknown", {f["check"] for f in doctor.run(self.k)})
        self.assertEqual(self.status(w), "open")
