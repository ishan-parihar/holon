"""Skill evolution on any harness: agents propose skill files in their result; the kernel applies them."""

from holon.integrity import doctor
from tests.helpers import KernelCase

TARGET = "skill:holon-executor/SKILL.md"


class SkillEvolution(KernelCase):
    def setUp(self):
        super().setUp()
        self.export = self.tmp / "harness-skills"
        self.k.export_skills(self.export, {"holon": "holon"})
        self.base = self.k.skills.read("holon-executor")

    def change(self, content, plan_writes=(TARGET,), verify=True):
        work = self.add(dept="eng", task_class="skill-tuning")
        approval = self.plan(work, writes=list(plan_writes))
        self.k.approve(approval["id"])
        c = self.k.claim("execute", self.dev1)
        out = self.k.submit(c.run["id"], self.exec_env(skill_changes=[
            {"skill": "holon-executor", "file": "SKILL.md", "content": content, "why": "executors kept skipping docs"}]))
        if verify and not out.get("breach"):
            v = self.k.claim("verify", self.hod)
            out = self.k.submit(v.run["id"], {"verdict": "pass", "note": "the skill reads well"})
        return work, approval, out

    def test_a_verified_change_lands_in_the_department_layer_and_every_export(self):
        work, approval, out = self.change(self.base + "\nAlways attach a run log.\n")
        self.assertGreaterEqual(approval["grade"], 2, "changing how a department works is never routine")
        self.assertTrue(out["skill_changes"][0]["applied"], out)
        self.assertIn("Always attach a run log", self.k.skills.read("holon-executor", dept="eng"))
        self.assertNotIn("Always attach a run log", self.k.skills.read("holon-executor"), "other departments keep theirs")
        self.assertEqual(out["skill_changes"][0]["exported_to"], [str(self.export.resolve())])
        self.assertEqual(self.k.stale_skill_exports(), [])

    def test_a_change_the_plan_did_not_name_is_refused(self):
        work, _, out = self.change(self.base + "\nsneaky\n", plan_writes=["hello.txt"])
        self.assertTrue(out["breach"])
        self.assertIn("does not list skill:holon-executor/SKILL.md", out["breach"][0])
        self.assertNotIn("sneaky", self.k.skills.read("holon-executor", dept="eng"))

    def test_a_change_that_breaks_the_library_is_rolled_back(self):
        _, _, out = self.change("---\nname: something-else\ndescription: x\nkind: role\n---\nbroken\n")
        self.assertFalse(out["skill_changes"][0]["applied"])
        self.assertEqual(self.k.skills.read("holon-executor", dept="eng"), self.base)

    def test_doctor_notices_an_export_that_drifted(self):
        (self.export / "holon-executor" / "SKILL.md").write_text("edited by hand")
        self.assertIn("skills.export_stale", {f["check"] for f in doctor.run(self.k)})
