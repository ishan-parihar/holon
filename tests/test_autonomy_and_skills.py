"""Autonomy policy, the department genome (roles, profiles, slots), and the skills library."""

import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path

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
        c = case.k.claim("approve", case.ceo)
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
        self.assertEqual(self.k.work.work_of(ceo["proposal"])["status"], "cancelled")


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
        self.assertEqual(self.status(t), "done")
        res = self.k.work.last_execute_output(t["id"])
        self.assertEqual((res["verified"], res["verified_by"]), (None, "sampling"))
        self.assertEqual(self.k.work.executor_streak(self.dev1, "routine"), 2, "unverified work earns no trust")

    def test_untrusted_or_graded_work_is_always_verified(self):
        self.k.config["autonomy"] = {"trusted_after": 0, "verify_sample_rate": 0.0}
        t = self.add("bigger", dept="eng", task_class="routine", risk="normal")
        self.plan_and_approve(t, assignee=self.dev1, writes=[], grade="G1")
        c = self.k.claim("execute", self.dev1)
        self.assertFalse(self.k.submit(c.run["id"], self.exec_env())["verification_skipped"])

    def test_a_refused_success_is_never_closed_by_sampling(self):
        """H-03, back half: the fold puts refusals in the stored result's blockers, and a result
        with blockers keeps its verifier — sampling may close trusted work, never a decision."""
        self.k.config["autonomy"] = {"trusted_after": 1, "verify_sample_rate": 0.0}
        t, _, out = self._g0(1)                       # build trust: one verified pass
        self.assertFalse(out["verification_skipped"])
        self.k.verify(t["id"], "pass", "ok")
        t2, _, out2 = self._g0(2)
        self.assertTrue(out2["verification_skipped"], "control: a clean trusted success closes unverified")
        t3 = self.add("routine refused", dept="eng", task_class="routine", risk="low")
        self.plan_and_approve(t3, assignee=self.dev1, writes=[], grade="G0")
        c3 = self.k.claim("execute", self.dev1)
        out3 = self.k.submit(c3.run["id"], self.exec_env(
            refusals=[{"tool": "terminal", "reason": "rm -rf denied by policy", "rule": "writes"}]))
        self.assertFalse(out3.get("verification_skipped"), "a refused success keeps its verifier")
        self.assertEqual(self.status(t3), "open")
        self.assertEqual(self.pipeline_of(t3)[-1], ("verify", "ready"))


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
        self.assertEqual((lead["roles"], lead["stages"]), (["head", "steward"], ["shape", "approve", "retro"]))
        maker = self.k.org.hire(name="M", title="Ops engineer", dept="ops", job_description=JD, driver="fake",
                                reason="r", slot="maker")
        self.assertEqual(maker["stages"], ["plan", "execute"])
        self.assertIn("independent", self.k.org.staffing("ops")["warnings"][0])
        self.k.org.hire(name="C", title="Ops checker", dept="ops", job_description=JD, driver="fake",
                        reason="r", slot="checker")
        st = self.k.org.staffing("ops")
        self.assertTrue(st["complete"])
        self.assertEqual(st["warnings"], [])
        with self.assertRaises(OrgError):
            self.k.org.hire(name="X", title="X", dept="ops", job_description=JD, driver="fake", reason="r",
                            slot="nope")

    def test_profiles_keep_only_their_own_pipeline_keys(self):
        g = genome.validate({"name": "x", "pipeline": {"research": "always"}, "slots": [
            {"name": "a", "roles": ["head", "steward", "researcher"]}, {"name": "b", "roles": ["planner", "executor"]},
            {"name": "c", "roles": ["verifier"]}]})
        self.assertEqual(g["pipeline"], {"research": "always"})
        with self.assertRaises(genome.GenomeError):
            genome.validate({**g, "pipeline": {"research": "sometimes"}})
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

    def test_a_run_carries_its_departments_resolved_skills(self):
        """R-09: a body that loads skills from disk must see one resolved library — a department
        fork is the only version of its skill it is offered, never a standard twin beside it."""
        self.k.skills.fork("holon-planner", dept="eng")
        p = self.home.root / "skills" / "@eng" / "holon-planner" / "SKILL.md"
        p.write_text(p.read_text() + "\nENG FORK: this department's own planner skill.\n")
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        brief = json.loads((c.brief_dir / "brief.json").read_text())
        self.assertEqual(brief["skills"], str(c.brief_dir / "skills"))
        exported = Path(brief["skills"])
        self.assertIn("ENG FORK", (exported / "holon-planner" / "SKILL.md").read_text(),
                      "the run's copy is the department's fork, not the standard library")
        self.assertEqual([x.name for x in exported.glob("holon-planner*")], ["holon-planner"],
                         "one resolved holon-planner per run — no standard twin beside the fork")

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
        rc, out = run("skill", "export", "--to", str(self.tmp / "exp"), "--name", "holon-executor")
        text = (self.tmp / "exp" / "holon-executor" / "briefs" / "execute.md").read_text()
        self.assertIn("${HOLON:-holon} run heartbeat", text, "exported skills must not name one instance")
        self.assertNotIn(str(self.home.root), text)


if __name__ == "__main__":
    unittest.main()
