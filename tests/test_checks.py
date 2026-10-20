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

    def test_an_employee_may_only_claim_the_task_classes_their_record_names(self):
        """`hr hire --task-classes` was write-only: the employee's classes were stored and read by
        nothing, so the `dept:class` namespace named a class without binding it to a gate. An
        employee whose record names classes may pick up a stage whose work carries one of those
        classes, and not one that carries another."""
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        # a specialist whose record names one class; the other engineers' records name none
        spec = self.k.org.hire(name="Su", title="Greeting engineer", dept="eng", job_description="jd",
                               roles=["planner"], driver="fake", reason="r", status="active",
                               task_classes=["greeting"])["id"]
        self.assertEqual(self.k.org.employee(spec)["task_classes"], ["greeting"])

        # work in a class the specialist holds is claimable
        self.add(dept="eng", task_class="greeting")
        c = self.k.claim("plan", spec)
        self.assertIsNotNone(c, "an employee may claim a stage in a class their record names")
        self.k.submit(c.run["id"], self.plan_env(writes=[]))

        # work in a class the specialist does not hold is not claimable by them
        self.add(dept="eng", task_class="docs")
        self.assertIsNone(self.k.claim("plan", spec),
                          "an employee may not claim a stage in a class their record does not name")
        # but Bo (no classes named on his record) may: the record has not narrowed him
        self.assertIsNotNone(self.k.claim("plan", self.hod),
                             "an employee whose record names no class may claim any stage")

    def test_a_narrowed_employee_still_claims_the_work_assigned_to_them(self):
        """The class gate narrows what an employee *picks up*, never what the Chair *directs*. If it
        also blocked assigned work, a Chair's assignment to a specialist in a class they do not hold
        would strand the work: nobody else may claim assigned work, so it would wait forever."""
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        spec = self.k.org.hire(name="Su", title="Greeting engineer", dept="eng", job_description="jd",
                               roles=["planner"], driver="fake", reason="r", status="active",
                               task_classes=["greeting"])["id"]
        # the Chair assigns the specialist work in a class they do not hold
        work = self.add(dept="eng", task_class="docs", assignee=spec)
        c = self.k.claim("plan", spec)
        self.assertIsNotNone(c, "work assigned to an employee stays theirs, whatever their classes")
        self.assertEqual(c.task["parent_id"], work["id"])

    def test_a_class_nobody_in_reach_holds_is_reported_not_left_waiting(self):
        """A stage whose class no employee in reach holds can never be claimed. The kernel's own
        doctor already reports a stage nobody can claim; it must know about the class gate too.
        Every employee in reach must be narrowed away for the stage to be unreachable — one
        unnarrowed colleague keeps it claimable."""
        from holon.integrity import doctor
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        # every planner in eng is narrowed to a class the work does not carry
        self.k.org.hire(name="Su", title="Greeting engineer", dept="eng", job_description="jd",
                        roles=["planner"], driver="fake", reason="r", status="active",
                        task_classes=["greeting"])["id"]
        self.k.org.conn.execute("UPDATE employees SET task_classes=? WHERE id=?",
                                ('["greeting"]', self.hod))
        self.add(dept="eng", task_class="poetry")
        findings = {f["check"] for f in doctor.run(self.k)}
        self.assertIn("classes.unstaffed", findings,
                      "a stage whose class no employee in reach holds is reported, not left waiting")

    def test_an_unnarrowed_employee_keeps_a_classed_stage_claimable(self):
        """The class gate narrows only the employees whose record names classes. A colleague whose
        record names none may still pick the work up — so the doctor must not report the stage as
        unstaffable. (The first cut of this check reported every classed stage on any instance
        whose employees carried no classes at all: a false positive on a healthy organization.)"""
        from holon.integrity import doctor
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        # work in a class the specialist does not hold: Bo (unnarrowed) may still claim it
        self.add(dept="eng", task_class="docs")
        self.assertIsNotNone(self.k.claim("plan", self.hod),
                             "an employee whose record names no class may claim any stage")
        findings = {f["check"] for f in doctor.run(self.k)}
        self.assertNotIn("classes.unstaffed", findings,
                         "an unnarrowed employee in reach keeps the stage claimable")
        # but when the only colleague is narrowed away, the stage is reported
        self.k.org.hire(name="Wa", title="Docs engineer", dept="eng", job_description="jd",
                        roles=["planner"], driver="fake", reason="r", status="active",
                        task_classes=["greeting"])
        self.k.org.conn.execute("UPDATE employees SET task_classes=? WHERE id=?",
                                ('["greeting"]', self.hod))
        self.add(dept="eng", task_class="docs")
        findings = {f["check"] for f in doctor.run(self.k)}
        self.assertIn("classes.unstaffed", findings,
                      "a stage whose only candidate is narrowed away is reported")

    def test_a_hire_may_not_name_a_task_class_the_taxonomy_does_not_carry(self):
        """A typo in `--task-classes` would narrow the employee to a class that exists nowhere:
        they could claim nothing, and the error would be invisible. The hire is refused instead."""
        from holon.execution.org import OrgError
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        with self.assertRaises(OrgError) as ctx:
            self.k.org.hire(name="Su", title="Engineer", dept="eng", job_description="jd",
                            roles=["planner"], driver="fake", reason="r", status="active",
                            task_classes=["greetings"])
        self.assertIn("greetings", str(ctx.exception))
        # a class the taxonomy carries is accepted
        e = self.k.org.hire(name="Su", title="Engineer", dept="eng", job_description="jd",
                            roles=["planner"], driver="fake", reason="r", status="active",
                            task_classes=["greeting"])
        self.assertEqual(e["task_classes"], ["greeting"])

    def test_an_employee_is_told_the_task_classes_they_hold(self):
        """The class gate is only fair if the employee knows it: the identity card names the
        classes they may pick up, and says that assigned work is theirs whatever its class."""
        self.configure('[classes]\norg = ["incident"]\neng = ["greeting", "docs"]\n')
        spec = self.k.org.hire(name="Su", title="Greeting engineer", dept="eng", job_description="jd",
                               roles=["planner"], driver="fake", reason="r", status="active",
                               task_classes=["greeting"])["id"]
        card = self.k.identity(spec)
        self.assertIn("greeting", card)
        self.assertIn("whatever its class", card)
        # an employee whose record names no class is not narrowed, and the card does not say so
        self.assertNotIn("Task classes you perform", self.k.identity(self.hod))

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
