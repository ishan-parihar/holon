"""Autonomy policy, the department genome (roles, profiles, slots), and the skills library."""

import io
import json
import unittest
from contextlib import redirect_stdout

from holon import cli
from holon.execution import autonomy, genome
from holon.execution.org import OrgError
from tests.helpers import JD, KernelCase

STREAK = "[grading]\nnovelty_min_verified = 0\n[autonomy]\npromote_after = 2\n"


def streak(case, n=2):
    out = None
    for i in range(n):
        t = case.add(f"tidy {i}", dept="eng", task_class="ops", risk="low")
        c = case.k.claim("plan", case.hod, task_id=t["id"])
        case.k.submit(c.run["id"], case.plan_env(grade="G1", writes=[], assignee=case.dev1))
        c = case.k.claim("review", case.ceo)
        case.k.submit(c.run["id"], {"decision": "approve", "note": "fine"})
        c = case.k.claim("execute", case.dev1)
        case.k.submit(c.run["id"], case.exec_env())
        c = case.k.claim("verify", case.dev2)
        out = case.k.submit(c.run["id"], {"verdict": "pass", "note": "ok"})
    return out


class Policy(unittest.TestCase):
    def test_presets_layers_and_department_overrides(self):
        cfg = {"autonomy": {"verify_sample_rate": 0.5, "dept": {"ops": {"mode": "autonomous", "trusted_after": 3}}}}
        org = autonomy.policy(cfg)
        self.assertEqual((org["mode"], org["widen_approver"], org["verify_sample_rate"]), ("supervised", "chair", 0.5))
        ops = autonomy.policy(cfg, "ops")
        self.assertEqual((ops["mode"], ops["widen_approver"], ops["auto_promote_cap"], ops["trusted_after"]),
                         ("autonomous", "auto", 4, 3))
        self.assertEqual(autonomy.policy(cfg, "other")["mode"], "supervised")

    def test_legacy_authority_section_and_validation(self):
        self.assertEqual(autonomy.policy({"authority": {"promote_after": 4}})["promote_after"], 4)
        with self.assertRaises(ValueError):
            autonomy.policy({"autonomy": {"mode": "yolo"}})
        with self.assertRaises(ValueError):
            autonomy.policy({"autonomy": {"verify_sample_rate": 2}})

    def test_sampling_is_deterministic_and_close_to_rate(self):
        hits = sum(autonomy.sampled(f"k{i}", 0.2) for i in range(5000))
        self.assertTrue(900 < hits < 1100, hits)
        self.assertEqual(autonomy.sampled("x", 0.2), autonomy.sampled("x", 0.2))
        self.assertFalse(any(autonomy.sampled(f"k{i}", 0.0) for i in range(100)))


class AutonomousPromotion(KernelCase):
    config = STREAK + "mode = \"autonomous\"\n"

    def test_autonomous_mode_widens_without_a_proposal(self):
        change = streak(self)["authority_change"]
        self.assertTrue(change["applied"])
        self.assertEqual((change["from"], change["to"]), (2, 3), "autonomous: the CEO starts at G2 and earns G3")
        self.assertEqual(self.k.org.authority_of(f"emp:{self.ceo}", "ops"), 3)


class DelegatedWidening(KernelCase):
    config = "[autonomy]\nmode = \"delegated\"\n"

    def test_ceo_decides_widenings_but_never_its_own(self):
        hod = self.k.set_authority(f"emp:{self.hod}", "*", 2, "asked by the HOD", requested_by=f"emp:{self.hod}")
        self.assertEqual((hod["applied"], hod["approver"]), (False, f"emp:{self.ceo}"))
        ceo = self.k.set_authority(f"emp:{self.ceo}", "*", 3, "asked by the CEO", requested_by=f"emp:{self.ceo}")
        self.assertEqual(ceo["approver"], "chair")
        again = self.k.set_authority(f"emp:{self.hod}", "*", 2, "asked twice", requested_by=f"emp:{self.hod}")
        self.assertEqual(again["proposal"], hod["proposal"], "one pending proposal per change")
        narrowed = self.k.set_authority(f"emp:{self.ceo}", "*", 0, "incident", requested_by="kernel")
        self.assertTrue(narrowed["applied"], "narrowing never waits")
        self.k.reject(ceo["proposal"], "not yet")
        self.assertEqual(self.k.work.get_task(self.k.work.get_plan(ceo["proposal"])["task_id"])["status"], "cancelled")


class VerifySampling(KernelCase):
    config = "[grading]\nnovelty_min_verified = 0\n"

    def _g0(self, i, rate_hit=None):
        t = self.add(f"routine {i}", dept="eng", task_class="routine", risk="low")
        self.plan_and_approve(t, assignee=self.dev1, writes=[], grade="G0")
        c = self.k.claim("execute", self.dev1)
        return t, c, self.k.submit(c.run["id"], self.exec_env())

    def test_trusted_g0_is_verified_on_a_sample_only(self):
        self.k.config["autonomy"] = {"trusted_after": 2, "verify_sample_rate": 0.0}
        for i in range(2):  # build trust: verified passes
            t, _, out = self._g0(i)
            self.assertFalse(out["verification_skipped"])
            self.k.verify(t["id"], "pass", "ok")
        t, c, out = self._g0(9)
        self.assertTrue(out["verification_skipped"])
        self.assertEqual(self.k.work.get_task(t["id"])["status"], "done")
        res = self.k.work.result_for_run(c.run["id"])
        self.assertEqual((res["verified"], res["verified_by"]), (None, "sampling"))
        self.assertEqual(self.k.work.executor_streak(self.dev1, "routine"), 2, "unverified work earns no trust")

    def test_untrusted_or_graded_work_is_always_verified(self):
        self.k.config["autonomy"] = {"trusted_after": 0, "verify_sample_rate": 0.0}
        t = self.add("bigger", dept="eng", task_class="routine", risk="normal")
        self.plan_and_approve(t, assignee=self.dev1, writes=[], grade="G1")
        c = self.k.claim("execute", self.dev1)
        self.assertFalse(self.k.submit(c.run["id"], self.exec_env())["verification_skipped"])


class Genome(KernelCase):
    def test_profiles_validate(self):
        for name in ("light", "standard", "executive"):
            genome.load_profile(name)
        with self.assertRaises(genome.GenomeError):
            genome.validate({"name": "x", "slots": [{"name": "a", "roles": ["executor", "verifier"]}]})
        with self.assertRaises(genome.GenomeError):
            genome.validate({"name": "x", "slots": [{"name": "a", "roles": ["head"]}]})

    def test_light_department_hires_into_slots(self):
        self.k.org.create_dept("ops", "Operations", "Run things", profile=genome.load_profile("light"))
        st = self.k.org.staffing("ops")
        self.assertEqual([s["slot"] for s in st["slots"]], ["lead", "maker", "checker"])
        self.assertFalse(st["complete"])
        lead = self.k.org.hire(name="L", title="Head of Ops", dept="ops", job_description=JD, driver="fake",
                               reason="r", slot="lead")
        self.assertTrue(lead["is_head"])
        self.assertEqual((lead["roles"], lead["duties"]), (["head", "steward"], ["review"]))
        maker = self.k.org.hire(name="M", title="Ops engineer", dept="ops", job_description=JD, driver="fake",
                                reason="r", slot="maker")
        self.assertEqual(maker["duties"], ["execute", "plan"])
        self.assertIn("independent", self.k.org.staffing("ops")["warnings"][0])
        self.k.org.hire(name="C", title="Ops checker", dept="ops", job_description=JD, driver="fake",
                        reason="r", slot="checker")
        st = self.k.org.staffing("ops")
        self.assertTrue(st["complete"])
        self.assertEqual(st["warnings"], [])
        with self.assertRaises(OrgError):
            self.k.org.hire(name="X", title="X", dept="ops", job_description=JD, driver="fake", reason="r",
                            slot="nope")

    def test_legacy_employees_have_inferred_roles(self):
        self.assertEqual(self.k.org.employee(self.hod)["roles"], ["head", "planner", "verifier"])
        self.assertEqual(self.k.org.dept("executive")["profile"], "executive")


class Skills(KernelCase):
    def test_stdlib_is_valid_and_covers_every_role(self):
        lib = self.k.skills
        self.assertEqual(lib.check(roles=list(genome.ROLES)), [])
        for role, spec in genome.ROLES.items():
            self.assertIn(spec["skill"], lib.names(), role)
            self.assertIn(role, lib.meta(spec["skill"])["roles"])

    def test_department_tailoring_reaches_the_brief(self):
        lib = self.k.skills
        lib.fork("holon-planner", dept="eng", file="briefs/plan.md")
        p = self.home.root / "skills" / "@eng" / "holon-planner" / "briefs" / "plan.md"
        p.write_text(p.read_text() + "\nENG HOUSE RULE: always cite a ticket.\n")
        self.assertEqual(lib.resolve("holon-planner", "briefs/plan.md", "eng")[0], "dept:eng")
        self.assertEqual(lib.resolve("holon-planner", "briefs/plan.md", "ops")[0], "stdlib")
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        self.assertIn("ENG HOUSE RULE", (c.brief_dir / "BRIEF.md").read_text())
        self.assertEqual(json.loads((c.brief_dir / "brief.json").read_text())["skill"], "holon-planner")

    def test_invoke_renders_and_records_use(self):
        r = self.k.invoke_skill("holon-employee", employee_id=self.dev1)
        self.assertNotIn("$holon", r["text"])
        self.assertIn(str(self.home.root), r["text"])
        ev = self.k.work.events("holon-employee/SKILL.md")
        self.assertEqual((ev[0]["to_status"], ev[0]["actor"]), ("stdlib", f"emp:{self.dev1}"))

    def test_new_check_and_export(self):
        lib = self.k.skills
        lib.new("ops-runbook", "operation", "How ops restarts things", ["executor"], dept="eng")
        self.assertIn("ops-runbook", lib.names("eng"))
        self.assertNotIn("ops-runbook", lib.names())
        (self.home.root / "skills" / "@eng" / "ops-runbook" / "SKILL.md").write_text(
            "---\nname: ops-runbook\ndescription: d\nkind: operation\nroles: [wizard]\n---\nsee `references/x.md`\n")
        problems = lib.check("eng", list(genome.ROLES))
        self.assertTrue(any("wizard" in p for p in problems) and any("references/x.md" in p for p in problems))
        out = lib.export(self.tmp / "exported", {"holon": "holon --home X"}, ["holon-executor"])
        self.assertIn("holon --home X", (out[0] / "SKILL.md").read_text())
        self.assertTrue((out[0] / "briefs" / "execute.md").exists())

    def test_cli_skill_commands(self):
        def run(*argv):
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["--home", str(self.home.root), *argv])
            return rc, buf.getvalue()
        rc, out = run("skill", "check")
        self.assertEqual(rc, 0, out)
        rc, out = run("skill", "list", "--as", self.dev1)
        self.assertIn("holon-executor", out)
        self.assertNotIn("holon-planner", out)
        rc, out = run("skill", "invoke", "holon-steward", "references/retro.md")
        self.assertIn("Retro", out)
        rc, out = run("dept", "roles", "eng")
        self.assertIn("profile light", out)
        rc, out = run("charter", "autonomy")
        self.assertIn("supervised", out)


if __name__ == "__main__":
    unittest.main()
