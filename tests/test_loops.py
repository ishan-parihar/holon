"""R5: the feedback loops. Every loop has a sensor, a comparator, and an actuator that is work."""

from holon.integrity import doctor
from tests.helpers import JD, KernelCase


class Loops(KernelCase):
    pipeline = '[pipeline]\nresearch = "never"\nshape = "never"\n[healing]\ndoctor_every_min = 0\n'   # retro stays auto

    def setUp(self):
        super().setUp()
        o = self.k.org
        o.create_dept("ops", "Operations", "Run things")
        self.lead = o.hire(name="Lu", title="Head of Ops", dept="ops", job_description=JD, slot="lead",
                           driver="fake", reason="r", status="active")["id"]
        self.maker = o.hire(name="Mo", title="Ops engineer", dept="ops", job_description=JD, slot="maker",
                            driver="fake", reason="r", status="active")["id"]
        self.checker = o.hire(name="Ce", title="Ops checker", dept="ops", job_description=JD, slot="checker",
                              driver="fake", reason="r", status="active")["id"]

    def _executed(self, title="Restart the queue"):
        t = self.add(title, dept="ops", task_class="restart")
        self.plan_and_approve(t, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env())
        return t

    def test_failed_verification_triggers_a_retro_whose_actions_become_work(self):
        t = self._executed()
        self.k.verify(t["id"], "fail", "queue still stuck")
        self.assertIn(("retro", "ready"), self.pipeline_of(t))
        c = self.k.claim("retro", self.lead)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("History of this work", ctx)
        self.assertIn("queue still stuck", ctx)
        out = self.k.submit(c.run["id"], {"summary": "restart missed the worker", "cause": "plan skipped workers",
                                          "learnings": ["restart workers with the queue"],
                                          "actions": [{"title": "Add workers to the restart runbook",
                                                       "intent": "so restarts include workers"}]})
        new = self.k.work.get(out["created"][0])
        self.assertEqual((new["dept_id"], new["source"], new["task_class"]),
                         (self.k.org.dept("ops")["id"], "audit", "improvement"))
        self.assertTrue(any("restart workers" in i["content"] for i in self.k.memory.live_items([f"emp:{self.lead}"])))

    def test_one_rejection_is_feedback_two_are_a_pattern(self):
        t = self.add("Tune alerts", dept="ops", task_class="alerts")
        a = self.plan(t, planner=self.maker)
        self.k.reject(a["id"], "too broad")
        self.assertNotIn("retro", [s for s, _ in self.pipeline_of(t)])
        a = self.plan(t, planner=self.maker)
        self.k.reject(a["id"], "still too broad")
        self.assertIn(("retro", "ready"), self.pipeline_of(t))

    def test_departments_without_a_steward_get_no_retro(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        c = self.k.claim("execute", self.dev1)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "fail", "nope")
        self.assertNotIn("retro", [s for s, _ in self.pipeline_of(t)])

    def test_proposals_reach_the_steward_through_a_department_retro(self):
        t = self.add("Restart", dept="ops", task_class="restart")
        self.plan_and_approve(t, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env(proposals=["automate restarts"]))
        out = self.k.tick()
        retro = [x for x in out["triggered"] if "dept_retro" in x]
        self.assertEqual((retro[0]["dept"], retro[0]["proposals"]), ("ops", 1))
        self.assertEqual(self.k.tick()["triggered"], [], "one department retro at a time")
        c = self.k.claim("retro", self.lead)
        self.assertIn("automate restarts", (c.brief_dir / "context.md").read_text())
        self.k.submit(c.run["id"], {"summary": "s", "cause": "c"})
        self.assertEqual(self.status(retro[0]["dept_retro"]), "done")
        self.assertEqual(self.k.work.messages(to_refs=["dept:ops"], kind="proposal", unacked=True), [])

    def test_a_rate_past_its_bound_makes_a_department_retro_due(self):
        self.k.config["metrics"]["min_samples"] = 2
        t = self.add("Tune alerts", dept="ops", task_class="alerts")
        self.k.reject(self.plan(t, planner=self.maker)["id"], "too broad")
        self.assertFalse([x for x in self.k.tick()["triggered"] if "dept_retro" in x], "one sample is not a rate")
        self.k.reject(self.plan(t, planner=self.maker)["id"], "still too broad")
        retro = [x for x in self.k.tick()["triggered"] if "dept_retro" in x]
        self.assertEqual((retro[0]["dept"], retro[0]["proposals"]), ("ops", 0), "no proposal was needed")
        self.assertIn("rejection_rate 1.0 is above 0.5 over 2", self.k.work.get(retro[0]["dept_retro"])["intent"])

    def test_a_stage_nobody_can_claim_is_reported_not_left_waiting(self):
        from holon.integrity import doctor
        w = self.add("Unplannable", dept="ops", task_class="restart")
        self.k.org.reassign(self.maker, "no planner left", roles=["executor"])
        unstaffed = [f["message"] for f in doctor.run(self.k) if f["check"] == "graph.unstaffed"]
        self.assertTrue(any("performs 'plan'" in m for m in unstaffed), unstaffed)
        t = self.add("Restart", dept="ops", task_class="restart")
        self.k.org.reassign(self.maker, "planner again", roles=["planner", "executor"])
        self.plan_and_approve(t, planner=self.maker, assignee=self.maker, writes=[])
        self.k.org.set_status(self.maker, "paused", "on leave")
        unstaffed = [f["message"] for f in doctor.run(self.k) if f["check"] == "graph.unstaffed"]
        self.assertTrue(any("is not active" in m for m in unstaffed), unstaffed)
        self.assertIsNotNone(w)

    def test_runs_that_keep_dying_open_an_incident(self):
        t = self.add("Flaky", dept="ops", task_class="restart")
        for _ in range(2):
            c = self.k.claim("plan", self.maker, task_id=t["id"])
            self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z' WHERE id=?", (c.run["id"],))
            out = self.k.tick()
        incident = [x for x in out["triggered"] if "incident" in x]
        self.assertEqual(len(incident), 1)
        work = self.k.work.get(incident[0]["incident"])
        self.assertEqual((work["task_class"], work["dept_id"]), ("incident", self.k.org.dept("executive")["id"]))

    def test_a_stage_whose_runs_keep_failing_opens_an_incident(self):
        self.install_fake_driver("fake", mode="garbage")  # e.g. a harness binary that rejects the driver's flags
        t = self.add("Doomed", dept="ops", task_class="restart")
        for _ in range(3):
            self.assertTrue(self.k.dispatch(self.maker, "plan", t["id"])["breach"])
        incident = [x for x in self.k.tick()["triggered"] if "incident" in x]
        self.assertEqual(len(incident), 1)
        self.assertIn("keep failing", incident[0]["title"])
        self.assertFalse([x for x in self.k.tick()["triggered"] if "incident" in x], "one incident, not one per tick")
        self.assertIsNone(self.k.dispatch(self.maker, "plan", t["id"]), "it waits before the next try")
        self.k.wconn.execute("UPDATE runs SET ended_at='2000-01-01T00:00:00.000000Z'")
        self.assertIsNotNone(self.k.dispatch(self.maker, "plan", t["id"]), "and is tried again after its back-off")

    def test_probation_ends_with_a_decision_by_the_head(self):
        newbie = self.k.org.hire(name="Nu", title="Ops engineer", dept="ops", job_description=JD, roles=["executor"],
                                 driver="fake", reason="growth")["id"]
        self.k.wconn.execute("UPDATE employees SET hired_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (newbie,))
        review = [x for x in self.k.tick()["triggered"] if "probation_review" in x][0]
        approval = self.k.work.get(review["probation_review"])
        self.assertEqual(approval["approver"], f"emp:{self.lead}")
        self.k.approve(approval["id"], "good start")
        self.assertEqual(self.k.org.employee(newbie)["status"], "active")
        self.assertFalse([x for x in self.k.tick()["triggered"] if "probation_review" in x])

    def test_metrics_pair_every_sensor_with_a_counter(self):
        t = self._executed()
        self.k.verify(t["id"], "fail", "x")
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env())
        self.k.verify(t["id"], "pass", "ok")
        m = self.k.metrics("ops")
        self.assertEqual(m["rework_rate"]["value"], 1.0)
        self.assertEqual(m["first_pass_rate"]["value"], 0.0)
        self.assertEqual(m["verify_pass_rate"]["value"], 0.5)
        for v in m.values():
            if isinstance(v, dict) and "value" in v:
                self.assertIn(v["counter"], m)


class Notifications(KernelCase):
    def test_the_chair_hears_once_about_what_newly_needs_them(self):
        out = self.tmp / "sent.txt"
        self.k.config["notify"] = {"command": f"printf '%s\\n---\\n' {{message}} >> {out}"}
        self.assertIsNone(self.k.tick()["notified"], "nothing new yet")
        self.plan(self.add("Rotate keys", dept="eng"))
        n = self.k.tick()["notified"]
        self.assertEqual((n["items"], n["sent"]), (1, True))
        self.assertIn("approve? Rotate keys", out.read_text())
        self.assertIsNone(self.k.tick()["notified"], "each item is announced once")
        self.k.config["notify"] = {"command": "exit 3"}
        self.plan(self.add("Second", dept="eng"))
        self.assertFalse(self.k.tick()["notified"]["sent"])
        self.assertTrue(any(e["entity"] == "notify" and e["to_status"] == "failed" for e in self.k.work.events()))

    def test_a_failed_delivery_is_owed_again_on_the_next_heartbeat(self):
        out = self.tmp / "sent.txt"
        self.k.config["notify"] = {"command": "exit 3"}
        self.plan(self.add("Rotate keys", dept="eng"))
        self.assertFalse(self.k.tick()["notified"]["sent"])
        self.assertEqual(self.k.tick()["notified"]["items"], 1, "a failed send does not mark the item seen")
        self.k.config["notify"] = {"command": f"printf '%s\\n---\\n' {{message}} >> {out}"}
        self.assertEqual(self.k.tick()["notified"], {"items": 1, "sent": True})
        self.assertIn("approve? Rotate keys", out.read_text())
        self.assertIsNone(self.k.tick()["notified"], "once delivered, it is announced once")


class DoctorOnTheHeartbeat(KernelCase):
    pipeline = '[pipeline]\nresearch = "never"\nshape = "never"\nretro = "never"\n[healing]\ndoctor_every_min = 30\n'

    def test_a_doctor_error_becomes_one_incident_and_the_check_waits_its_interval(self):
        self.k.auth.init_chair_key()
        self.k.wconn.execute("UPDATE events SET note='tampered' WHERE seq=(SELECT min(seq) FROM events)")
        first = [x for x in self.k.tick()["triggered"] if "incident" in x]
        self.assertEqual([x["title"] for x in first], ["Incident: doctor reports audit.chain"])
        self.assertFalse([x for x in self.k.tick()["triggered"] if "incident" in x], "doctor waits doctor_every_min")


class BuildRegression(Loops):
    """H10 (phase H): the build that ran a result is recorded on the run, the rubric judge's
    judgments carry it, and a build whose pass rate drops materially below the previous
    build's opens incident work — a regressed harness is visible from judgments alone."""

    def _judged(self, work, build, n, passed, at):
        for i in range(n):
            self.k.wconn.execute(
                "INSERT INTO judgments (work_id, playbook, version, criterion, p, passed, build, created_at)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (work["id"], None, None, "does the job", 0.9 if passed else 0.2, int(passed), build, at))

    def test_a_regressed_build_opens_incident_work(self):
        t = self.add(dept="ops", task_class="restart")
        self._judged(t, "harness-7", 12, True, "2026-10-10T09:00:00Z")    # the good build
        self._judged(t, "harness-7", 12, True, "2026-10-11T09:00:00Z")
        self._judged(t, "harness-8", 12, False, "2026-10-12T09:00:00Z")   # the regressed one
        out = self.k.triggers()
        inc = next(i for i in out if "harness-8" in i["title"])
        w = self.k.work.get(inc["incident"])
        self.assertEqual(w["task_class"], "incident", "incident work")
        self.assertIn("from 100% on build harness-7 to 0% on build harness-8", w["intent"], "the drop is named")
        self.assertTrue(all("harness-8" not in i["title"] for i in self.k.triggers()), "one incident, not one a sweep")

    def test_a_better_build_or_too_few_judgments_is_not_an_incident(self):
        t = self.add(dept="ops", task_class="restart")
        self._judged(t, "harness-7", 12, False, "2026-10-10T09:00:00Z")
        self._judged(t, "harness-8", 12, True, "2026-10-11T09:00:00Z")   # improved: no incident
        self.assertFalse([i for i in self.k.triggers() if "regressed" in i.get("title", "")])
        self._judged(t, "harness-9", 3, False, "2026-10-12T09:00:00Z")   # too few judgments to judge
        self.assertFalse([i for i in self.k.triggers() if "regressed" in i.get("title", "")])


class GuardLoop(Loops):
    """K-2: an incident's verified fix registers its `guard` — which NAMES a [checks.class]
    entry (the Chair's command; the fixer never supplies shell, because the heartbeat runs guards
    as its own user). The heartbeat runs registered guards, and a failure reopens the incident —
    a fixed defect is re-checked forever, not remembered. A closed incident with neither guard
    nor waiver is a doctor warning."""

    config = Loops.config + '\n[checks.class.disk]\ncommands = ["test -e ."]\n'

    def _fixed_incident(self, guard=None):
        w = self.add(dept="ops", task_class="incident")
        self.plan_and_approve(w, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        self.k.submit(c.run["id"], self.exec_env(**({"guard": guard} if guard else {})))
        v = self.k.claim("verify", self.checker)
        out = self.k.submit(v.run["id"], {"verdict": "pass", "note": "fixed"})
        return w, out

    def test_a_verified_incident_fix_registers_its_guard(self):
        w, out = self._fixed_incident(guard={"name": "disk free", "check": "disk", "timeout": 5})
        self.assertEqual(self.k.work.get(w["id"])["status"], "done")
        row = self.k.wconn.execute("SELECT name, \"check\" FROM guards WHERE work_id=?", (w["id"],)).fetchone()
        self.assertEqual(tuple(row), ("disk free", "disk"), "the guard, with provenance on the incident")

    def test_a_guard_naming_an_unknown_check_class_is_refused_at_submit(self):
        w = self.add(dept="ops", task_class="incident")
        self.plan_and_approve(w, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        out = self.k.submit(c.run["id"], self.exec_env(guard={"name": "disk free", "check": "no-such"}))
        self.assertTrue(any("is not a [checks.class] entry" in p for p in out["breach"]),
                        "the fixer is told, in the breach note")
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM guards").fetchone()[0], 0,
                         "nothing registered: the heartbeat never runs an employee's command")
        self.assertNotEqual(self.status(w), "done", "a refused guard does not close its incident")

    def test_a_failing_guard_reopens_the_incident_once(self):
        self.k.config["checks"]["class"]["disk"] = {"commands": ["false"]}
        w, out = self._fixed_incident(guard={"name": "disk free", "check": "disk"})
        opened = self.k._guard_triggers()
        self.assertEqual(len(opened), 1, "the regression opens incident work")
        inc = self.k.work.get(opened[0]["incident"])
        self.assertIn("fixed and verified once; it is back", inc["intent"], "the provenance is named")
        self.assertEqual(self.k._guard_triggers(), [], "one open incident per guard, not one a sweep")

    def test_a_dead_check_class_fails_its_guard(self):
        w, out = self._fixed_incident(guard={"name": "disk free", "check": "disk"})
        del self.k.config["checks"]["class"]["disk"]
        opened = self.k._guard_triggers()
        self.assertEqual(len(opened), 1, "a guard whose check class vanished is a failure, not a silent skip")
        self.assertIn("no longer exists", self.k.work.get(opened[0]["incident"])["intent"])

    def test_a_closed_incident_needs_a_guard_or_a_waiver(self):
        w, out = self._fixed_incident()                     # fixed, but registered no guard
        findings = [f for f in doctor.run(self.k) if f["check"] == "incident.guard"]
        self.assertEqual(len(findings), 1, "doctor warns: the fix leaves nothing that re-checks it")
        self.k.auth._put(f"incident:{w['id']}:waived", "the disk fills and empties by design")
        self.assertEqual([f for f in doctor.run(self.k) if f["check"] == "incident.guard"], [],
                         "a recorded waiver is a decision, not an omission")


class SetDriver(Loops):
    """When a harness is retired, the people bound to it used to be unusable: the kernel had no
    driver-change path at all, so the only way to move them was fire + re-hire — losing their
    authority, their memory and their record. `hr set-driver` rebinds them, and it refuses a
    driver that cannot serve the stages their roles need, so nobody is stranded on a partial one."""

    def _hire_on(self, driver, slot="maker"):
        return self.k.org.hire(name="New", title="Engineer", dept="ops", job_description=JD,
                               slot=slot, driver=driver, reason="r", status="active")["id"]

    def test_an_employee_may_be_rebound_to_a_driver_that_serves_their_stages(self):
        e = self._hire_on("fake")
        self.install_fake_driver("other")
        out = self.k.org.set_driver(e, "other", "fake is retired")
        self.assertEqual(out["driver"], "other")
        self.assertEqual(self.k.org.employee(e)["driver"], "other",
                         "the change is what a later reader sees, not just the returned row")
        ev = self.k.wconn.execute("SELECT from_status, to_status FROM events WHERE entity_id=? AND entity='employee'",
                                  (e,)).fetchall()
        self.assertIn((("fake"), "other"), [tuple(r) for r in ev],
                      "the audit trail carries the rebinding, not a silent UPDATE")

    def test_an_employee_may_not_be_rebound_to_a_driver_that_is_not_installed(self):
        from holon.execution import org
        e = self._hire_on("fake")
        with self.assertRaises(org.OrgError) as caught:
            self.k.org.set_driver(e, "no-such", "typo")
        self.assertIn("no driver 'no-such'", str(caught.exception))
        self.assertEqual(self.k.org.employee(e)["driver"], "fake", "a refused change changes nothing")

    def test_an_employee_may_not_be_rebound_to_a_driver_that_cannot_serve_their_roles(self):
        from holon.execution import org
        maker = self._hire_on("fake", slot="maker")
        self.install_fake_driver("verify-only", extra='stages = ["verify"]\n')
        with self.assertRaises(org.OrgError) as caught:
            self.k.org.set_driver(maker, "verify-only", "cheaper")
        self.assertIn("does not serve", str(caught.exception))
        self.assertIn("plan", str(caught.exception), "the maker's own missing stage is named")
        self.assertEqual(self.k.org.employee(maker)["driver"], "fake",
                         "a partial driver is refused, so the employee is never stranded on one")

    def test_a_rebound_employee_still_claims_their_own_work(self):
        """The point of the command: the work they hold now runs on the new harness."""
        e = self._hire_on("fake", slot="maker")
        self.install_fake_driver("other")
        self.k.org.set_driver(e, "other", "fake is retired")
        t = self.add(dept="ops", task_class="restart")
        self.plan_and_approve(t, planner=self.maker, assignee=e, writes=[])
        c = self.k.claim("execute", e, driver="other")
        self.assertIsNotNone(c, "the rebound employee still claims the work assigned to them")
        self.assertEqual(self.k.work.get_run(c.run["id"])["driver"], "other",
                         "their work now runs on the new harness")
        self.assertEqual(self.k.org.employee(e)["driver"], "other")
