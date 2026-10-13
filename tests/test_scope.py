"""Scope: what a department's employees may reach ([scope] in holon.toml), and `hr reassign`."""

import json

from holon.execution import drivers
from holon.execution.org import OrgError
from holon.execution.service import Kernel
from holon.integrity import doctor, lint
from tests.helpers import KernelCase


class Scope(KernelCase):
    def configure(self, toml):
        self.home.config_file.write_text(self.pipeline + "\n" + self.config + "\n" + toml)
        self.k.close()
        self.k = Kernel(self.home)

    def test_department_sections_add_to_the_org_scope(self):
        self.configure('[scope]\ncommands = ["jq"]\n[scope.dept.eng]\nroots = ["~/shared"]\nact_commands = ["systemctl"]\n')
        eng, other = self.k.scope("eng"), self.k.scope("executive")
        self.assertEqual(eng["name"], "eng")
        self.assertEqual(eng["commands"], ["jq"])
        self.assertEqual(eng["act_commands"], ["systemctl"])
        self.assertFalse(eng["roots"][0].startswith("~"), "roots are expanded")
        self.assertEqual((other["name"], other["roots"]), ("org", []))
        self.assertEqual([s["name"] for s in self.k.scopes()], ["org", "eng"])

    def test_briefs_and_drivers_carry_the_scope(self):
        self.configure(f'[scope.dept.eng]\nroots = ["{self.tmp / "shared"}"]\ncommands = ["arch"]\n'
                       'act_commands = ["systemctl"]\n')
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        brief = json.loads((c.brief_dir / "brief.json").read_text())
        self.assertEqual(brief["scope"]["name"], "eng")
        md = (c.brief_dir / "BRIEF.md").read_text()
        self.assertIn("## Your reach", md)
        self.assertIn("`arch`", md)
        self.assertNotIn("systemctl", md, "act-only commands are not offered to observe stages")
        d = drivers.Driver(name="x", command="agent -a holon-{scope}-{posture}")
        self.assertEqual(drivers.render(d, brief)[0], "agent -a holon-eng-observe")

    def test_writes_inside_the_reach_are_in_bounds_and_beyond_it_go_to_the_chair(self):
        shared = self.tmp / "shared"
        self.configure(f'[grading]\nnovelty_min_verified = 0\n[scope.dept.eng]\nroots = ["{shared}"]\n')
        inside = self.plan(self.add(dept="eng", task_class="c", risk="low"), writes=[str(shared / "a.txt")], grade="G0")
        self.assertEqual(inside["grade"], 0, "a write inside the reach raises no floor")
        beyond = self.plan(self.add("b", dept="eng", task_class="c", risk="low"), writes=["/elsewhere/b.txt"], grade="G0")
        self.assertEqual(beyond["grade"], 3)

    def test_planners_are_told_the_grade_floor_and_the_approver(self):
        self.add(dept="eng", task_class="fresh", risk="low")
        md = (self.k.claim("plan", self.hod).brief_dir / "context.md").read_text()
        self.assertIn("## Grade floor (computed by the kernel)", md)
        self.assertIn("the floor is **G2**", md, "a novel task class floors at G2")
        self.assertIn("plan goes to `", md)

    def test_doctor_catches_a_sandbox_built_for_another_scope(self):
        self.install_fake_driver("fake", extra=f'scope_fingerprint = "{self.k.scope_fingerprint()}"')
        self.assertNotIn("scope.drift", {f["check"] for f in doctor.run(self.k)})
        self.configure('[scope.dept.eng]\nroots = ["/srv"]\n')
        self.assertIn("scope.drift", {f["check"] for f in doctor.run(self.k) if f["severity"] == "error"})

    def test_lint_checks_scope_sections(self):
        self.configure('[scope.dept.nope]\nroots = ["relative/path"]\nbogus = 1\n')
        msgs = [f["message"] for f in lint.run(self.k, ["schema"]) if f["rule"] == "schema.config"]
        self.assertTrue(any("no such department" in m for m in msgs), msgs)
        self.assertTrue(any("must be absolute" in m for m in msgs), msgs)
        self.assertTrue(any("bogus" in m for m in msgs), msgs)


class Reassign(KernelCase):
    def test_roles_follow_the_slots_and_headship_is_untouched(self):
        e = self.k.org.reassign(self.dev1, "light profile", slots=["maker"])
        self.assertEqual(e["roles"], ["executor", "planner"])
        hod = self.k.org.reassign(self.hod, "light profile", slots=["checker"])
        self.assertEqual(hod["roles"], ["head", "researcher", "verifier"])
        with self.assertRaises(OrgError):
            self.k.org.reassign(self.dev1, "coup", roles=["head"])
        self.assertTrue(self.k.org.staffing("eng")["slots"][1]["ok"], "the maker slot is now filled")


class SingleFlight(KernelCase):
    def test_the_concurrency_cap_is_checked_inside_the_claim(self):
        self.add(dept="eng")
        self.add("second", dept="eng")
        self.k.claim("plan", self.hod)
        self.assertIsNone(self.k.claim("plan", self.hod, max_running=1), "a second wake in the same tick must wait")
        self.assertIsNotNone(self.k.claim("plan", self.hod), "a pull-mode session decides for itself")
