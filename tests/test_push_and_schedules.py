"""Push mode through a driver (fake harness), concurrency, schedules, recurring plans, hiring."""

import contextlib
import io
import json
import multiprocessing as mp
import unittest

from holon.core.paths import Home
from holon.execution.service import Kernel
from holon.execution.work import TransitionError
from holon import cli
from holon.cli import FORBIDDEN
from tests.helpers import JD, KernelCase
from tests.test_lanes import LaneCase


class PushMode(KernelCase):
    def test_dispatch_through_driver_end_to_end(self):
        t = self.add(dept="eng", task_class="demo")
        out = self.k.dispatch(self.hod, "plan")
        self.assertEqual(out["approval"]["status"], "ready")
        self.k.approve(out["approval"]["id"])
        out = self.k.dispatch(self.dev1, "execute")
        self.assertEqual(self.pipeline_of(t)[-1], ("verify", "ready"))
        self.assertTrue((self.workdir / "hello.txt").exists())
        self.assertEqual(out["proposals_routed"], 1)
        out = self.k.dispatch(self.dev2, "verify")
        self.assertEqual(out["work"]["status"], "done")
        self.assertTrue(self.k.memory.live_items(["dept:eng"], kinds=["constraint"]))
        run = self.k.wconn.execute("SELECT id FROM runs WHERE kind='verify'").fetchone()[0]
        self.assertIn("[exit] 0", (self.home.run_dir(run) / "driver.log").read_text())

    def test_dispatch_without_a_stage_takes_whatever_the_roles_allow(self):
        self.add(dept="eng")
        out = self.k.dispatch(self.hod)
        self.assertEqual(out["stage"], "plan")

    def test_approve_stage_through_driver(self):
        self.k.org.set_authority(f"emp:{self.ceo}", "*", 4, "test: CEO may approve anything")
        t = self.add(dept="eng")
        self.k.dispatch(self.hod, "plan")
        out = self.k.dispatch(self.ceo, "approve")
        self.assertEqual(out["decider"], f"emp:{self.ceo}")
        self.assertEqual(self.pipeline_of(t)[-1], ("execute", "ready"))

    def test_read_only_guard_rejects_a_writing_observer(self):
        self.install_fake_driver("fake", mode="dirty-planner")
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertIn("read-only", out["breach"][0])
        self.assertEqual(self.pipeline_of(t), [("plan", "ready")])

    def test_harness_read_only_is_trusted_only_when_the_instance_says_so(self):
        self.install_fake_driver("fake", mode="dirty-planner", extra="read_only_enforced = true")
        self.add(dept="eng")
        self.assertIn("breach", self.k.dispatch(self.hod, "plan"), "defense in depth: the kernel still checks")
        self.k.config["planner"]["trust_harness_read_only"] = True
        self.add("again", dept="eng")
        self.assertNotIn("breach", self.k.dispatch(self.hod, "plan"))

    def test_caches_left_by_running_tests_are_not_a_breach(self):
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=self.workdir, check=True)
        (self.workdir / "a.py").write_text("x = 1\n")
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "add", "-A"], cwd=self.workdir, check=True)
        subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "i"], cwd=self.workdir,
                       check=True)
        self.install_fake_driver("fake", mode="pycache")
        self.add(dept="eng")
        self.assertNotIn("breach", self.k.dispatch(self.hod, "plan"))

    def test_a_shared_tree_others_commit_to_or_write_ignored_paths_in_is_not_a_breach(self):
        import subprocess
        from holon.execution import staleness
        git = lambda *a: subprocess.run(["git", "-C", str(self.workdir), *a], capture_output=True, check=True)  # noqa: E731
        git("init", "-q")
        (self.workdir / "notes.md").write_text("draft\n")
        before = staleness.snapshot(self.workdir)
        git("-c", "user.name=t", "-c", "user.email=t@t", "add", "-A")
        git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "auto")  # an auto-committer at work
        self.assertEqual(staleness.changes(before, staleness.snapshot(self.workdir)), [])
        self.home.config_file.write_text(self.pipeline + '[planner]\nguard_ignore = ["data/**"]\n')
        from holon.execution.service import Kernel
        self.k.close()
        self.k = Kernel(self.home)
        self.install_fake_driver("fake", mode="cron-writes")
        self.add(dept="eng")
        self.assertFalse(self.k.dispatch(self.hod, "plan").get("breach"))
        self.install_fake_driver("fake", mode="dirty-planner")
        self.add("second", dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertIn("scribble.txt", out["breach"][0], "a breach names what changed")

    def test_a_breach_after_the_agent_submitted_itself_is_recorded_not_fatal(self):
        self.install_fake_driver("fake", mode="dirty-planner+self-submit")
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out["submitted_by_agent"])
        self.assertIn("read-only", out["breach"][0])
        stage = self.k.work.stages(t["id"], "plan")[0]
        self.assertTrue(any("read-only breach" in (e["note"] or "") for e in self.k.work.events(stage["id"])))

    def test_stdout_json_driver(self):
        self.install_fake_driver("fake", result="stdout-json", mode="stdout")
        self.add(dept="eng")
        self.assertIn("approval", self.k.dispatch(self.hod, "plan"))

    def test_agent_may_submit_itself_with_its_run_token(self):
        self.install_fake_driver("fake", mode="self-submit")
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out.get("submitted_by_agent"))
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))

    def test_garbage_output_from_executor_is_wrapped(self):
        self.add(dept="eng")
        self.k.approve(self.k.dispatch(self.hod, "plan")["approval"]["id"])
        self.install_fake_driver("fake", result="stdout-json", mode="garbage")
        out = self.k.dispatch(self.dev1, "execute")
        self.assertTrue(out["breach"])
        self.assertIn("trust me", self.k.work.last_execute_output(out["work"]["id"])["documentation"])

    def test_a_stalling_body_records_that_it_produced_nothing(self):
        """Both harnesses failed the same way (2026-10-20): the body reasoned its way to
        `holon_submit`, kept exploring, and was killed by the timeout having written no result at
        all. The run failed, the stage recycled, and nothing in the work graph said why or what it
        had been doing — the operator's only evidence was driver.log. A stall is now a recorded
        event, so a repeated stall is visible instead of a stage that silently keeps recycling."""
        from pathlib import Path
        from holon.execution import briefs
        # a short deadline so the test runs in seconds: a stall lasts as long as the driver is
        # allowed, and the real plan lease (1800s) is asserted separately above
        self.install_fake_driver("fake", mode="stall", timeout_s=2)
        t = self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out["breach"], "a body that writes no result still breaches the contract")
        self.assertEqual(self.status(t), "open", "the stage is recycled for another attempt")
        run = self.k.wconn.execute("SELECT id FROM runs WHERE kind='plan'").fetchone()[0]
        log = (self.home.run_dir(run) / "driver.log").read_text()
        self.assertIn("holon_submit", log, "the body's own reasoning is preserved for review")
        notes = [r[0] for r in self.k.wconn.execute(
            "SELECT note FROM events WHERE entity='run' AND entity_id=?", (run,))]
        self.assertTrue(any("no envelope" in n for n in notes),
                        "a body that produced nothing says so in the audit trail")

    def test_the_runs_brief_names_the_schema_it_must_write(self):
        """The one artifact a body needs to answer correctly is the envelope's exact shape. It was
        rendered nowhere into the run and pointed at inside the distribution, which was also in the
        agent's read reach and simultaneously forbidden by the brief ("Holon's own source code is
        not an input") — so an operant body spent 66 tool calls hunting for a schema that existed
        and was visible. The schema is now written into the run, and every brief names it."""
        from pathlib import Path
        from holon.execution import briefs
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        d = briefs.load(self.home, c.run["id"])
        schema = Path(d["envelope"])
        self.assertTrue(schema.is_file(), "the run carries its envelope schema")
        body = json.loads(schema.read_text())
        self.assertEqual(body["$id"], "holon:plan-result", "rendered from the contract, not copied")
        self.assertIn("findings", json.dumps(body))
        brief = Path(d["brief_md"]).read_text()
        self.assertIn("envelope.schema.json", brief, "the brief names the schema it must write")
        self.assertIn("## Budget", brief, "the brief names the exploration budget")
        # the budget's rule lives in the one skill every employee loads, not in seven copies
        # of the brief — the distribution's own rubric flagged those as duplication and was right
        self.assertIn("How to spend your turn budget", self.k.invoke_skill("holon-employee")["text"])

    def test_a_drivers_deadline_never_exceeds_the_stages_lease(self):
        """Every shipped driver declares 3600s while a `plan` stage leases 1800s and a `verify` one
        1200s. With the driver slower than the lease, the sweep recovered the work while the harness
        was still running: the stage returned to ready under a live run, and two bodies then
        contended for one result.json. The lease is the real bound, so the driver takes the smaller.
        """
        from holon.execution import drivers
        drv = drivers.Driver(name="t", command="true", timeout_s=3600)
        self.assertEqual(drivers.deadline_for(drv, "plan"), 1800)
        self.assertEqual(drivers.deadline_for(drv, "verify"), 1200)
        self.assertEqual(drivers.deadline_for(drv, "execute"), 3600,
                         "a stage whose lease exceeds the driver's timeout keeps the driver's")
        tight = drivers.Driver(name="t2", command="true", timeout_s=300)
        self.assertEqual(drivers.deadline_for(tight, "execute"), 300,
                         "a driver tighter than every lease is honoured")

    def test_a_stalled_body_is_stopped_while_its_lease_is_still_live(self):
        """A body that overruns is stopped by the driver's own deadline, not by a lease sweep: the
        run is still open when it is killed, which is the only state in which its remaining output
        can be attributed to it. Before this, the kill (3600s) outlasted the plan lease (1800s), so
        the sweep recovered the work under a live run and two bodies contended for one
        result.json."""
        import time
        self.install_fake_driver("fake", mode="stall", timeout_s=2)
        work = self.add(dept="eng")
        t0 = time.monotonic()
        out = self.k.dispatch(self.hod, "plan")
        self.assertLess(time.monotonic() - t0, 1500, "the harness is killed, not waited out")
        run = self.k.wconn.execute("SELECT id FROM runs WHERE kind='plan'").fetchone()[0]
        log = (self.home.run_dir(run) / "driver.log").read_text()
        self.assertIn("[exit] 124", log, "stopped by the driver's own deadline")
        self.assertIn("holon_submit", log, "its exploration is what the log keeps")
        self.assertEqual(self.status(work), "open",
                         "the stage is still the organization's, with a claim released and retried")

    def test_a_running_harness_keeps_its_lease_and_a_hung_one_is_killed(self):
        import sys
        import time
        from holon.execution import briefs, drivers
        self.add(dept="eng")
        c = self.k.claim("plan", self.hod)
        brief = briefs.load(self.home, c.run["id"])
        beats = []
        slow = drivers.Driver(name="slow", command=f"{sys.executable} -c 'import time; time.sleep(1)'")
        out = drivers.run(self.home, slow, brief, alive=lambda: beats.append(1), alive_every_s=0.2)
        self.assertEqual(out["exit_code"], 0)
        self.assertGreaterEqual(len(beats), 3, "the kernel renews the lease while the harness works")
        hung = drivers.Driver(name="hung", command=f"{sys.executable} -c 'import time; time.sleep(30)'", timeout_s=1)
        t0 = time.monotonic()
        self.assertEqual(drivers.run(self.home, hung, brief, alive_every_s=0.2)["exit_code"], 124)
        self.assertLess(time.monotonic() - t0, 10, "a harness past its timeout is killed, shell and all")

    def test_a_harness_summary_is_unwrapped_and_its_usage_recorded(self):
        self.install_fake_driver("fake", result="stdout-json", mode="wrapped")
        self.add(dept="eng")
        out = self.k.dispatch(self.hod, "plan")
        self.assertFalse(out.get("breach"), out)
        run = self.k.wconn.execute("SELECT tokens_in, tokens_out, cost FROM runs").fetchone()
        self.assertEqual(tuple(run), (120, 7, 0.01))

    def test_a_harness_still_running_after_its_result_is_in_is_stopped(self):
        import time
        from unittest import mock
        from holon.execution import drivers
        self.install_fake_driver("fake", mode="linger",
                                 extra="usage_line = 'token_usage input=(?P<tokens_in>\\d+) output=(?P<tokens_out>\\d+)'")
        self.add(dept="eng")
        real = drivers.run
        with mock.patch.object(drivers, "run", lambda *a, **kw: real(*a, **{**kw, "alive_every_s": 0.5})):
            t0 = time.monotonic()
            out = self.k.dispatch(self.hod, "plan")
        self.assertTrue(out["submitted_by_agent"])
        self.assertLess(time.monotonic() - t0, 30, "the dispatcher does not wait out the harness's closing turn")
        run = self.k.wconn.execute("SELECT tokens_in, tokens_out FROM runs").fetchone()
        self.assertEqual(tuple(run), (2000, 100), "a stopped harness's per-call usage lines are summed")

    def test_concurrency_cap(self):
        self.add(dept="eng")
        self.add("second", dept="eng")
        self.k.claim("plan", self.hod)
        self.assertIsNone(self.k.dispatch(self.hod, "plan"))

    def test_paused_employee_cannot_work(self):
        self.k.org.set_status(self.hod, "paused", "testing")
        with self.assertRaises(RuntimeError):
            self.k.dispatch(self.hod, "plan")

    def test_legacy_driver_duties_map_to_stages(self):
        self.install_fake_driver("old", extra='duties = ["plan", "review"]')
        from holon.execution import drivers
        d = drivers.get(self.home, "old")
        self.assertEqual(d.stages, ["approve", "plan", "research", "retro", "shape"])


def _claimer(home_root, n, q):
    k = Kernel(Home(home_root), actor="worker")
    got = []
    for _ in range(n):
        c = k.claim("plan")
        if c:
            got.append(c.node["id"])
    k.close()
    q.put(got)


class Concurrency(KernelCase):
    def test_parallel_claims_never_double_claim(self):
        ids = {self.k.work.stages(self.add(f"t{i}", workdir=None)["id"])[0]["id"] for i in range(24)}
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        procs = [ctx.Process(target=_claimer, args=(self.home.root, 8, q)) for _ in range(6)]
        for p in procs:
            p.start()
        claimed = []
        for _ in procs:
            claimed += q.get(timeout=120)
        for p in procs:
            p.join(timeout=60)
        self.assertEqual(len(claimed), len(set(claimed)), "a stage was claimed twice")
        self.assertEqual(set(claimed), ids)


class Schedules(KernelCase):
    def test_wake_schedule_dispatches_in_the_foreground(self):
        s = self.k.schedules.add("wake", "*/5 * * * *", employee_id=self.hod, stage="plan")
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        t = self.add(dept="eng")
        out = self.k.tick(foreground=True)
        self.assertEqual(out["fired"][0]["dispatched"], True)
        self.assertEqual(self.pipeline_of(t)[-1], ("approve", "ready"))
        after = self.k.schedules.get(s["id"])
        self.assertGreater(after["next_run_at"], after["last_run_at"])
        self.assertEqual(self.k.tick(foreground=True)["fired"], [])

    def test_wake_schedule_requires_the_role(self):
        with self.assertRaises(ValueError):
            self.k.schedules.add("wake", "0 * * * *", employee_id=self.dev1, stage="verify")

    def test_recurring_plan_creates_preapproved_work(self):
        w = self.add("Daily digest", dept="eng", task_class="digest")
        approval = self.plan(w, writes=[], assignee=self.dev1)
        plan_id = self.k.work.needs(approval["id"])[0]
        with self.assertRaises(TransitionError):
            self.k.schedules.add("recurring", "0 9 * * *", template_id=plan_id)
        self.k.approve(approval["id"], note="ok as a daily template", recurring=True)
        s = self.k.schedules.add("recurring", "0 9 * * *", template_id=plan_id)
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        new_id = self.k.tick()["fired"][0]["recurring_work"]
        new = self.k.work.get(new_id)
        self.assertEqual((new["status"], new["source"], new["assignee"]), ("open", "schedule", None))
        self.assertEqual(self.k.work.approved_plan(new_id)["approval"]["decided_by"], f"recurring:{plan_id}")
        c = self.k.claim("execute", self.dev1, task_id=new_id)
        self.assertIsNotNone(c)

        def fire():
            self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
            return self.k.tick()["fired"][0]
        self.assertEqual(fire(), {"schedule": s["id"], "skipped": new_id}, "slow work does not pile up")
        self.k.work.cancel(w["id"], "no longer wanted")
        self.assertIn("disabled", fire())
        self.assertFalse(self.k.schedules.get(s["id"])["enabled"], "a cancelled template stops firing")


class Hiring(KernelCase):
    def test_hire_validates_and_head_takes_reports(self):
        with self.assertRaises(RuntimeError):
            self.k.org.hire(name="X", title="Y", dept="eng", job_description=JD, roles=["dancer"], driver="fake",
                            reason="r")
        with self.assertRaises(RuntimeError):
            self.k.org.hire(name="X", title="Y", dept="eng", job_description=JD, roles=["head"], driver="fake",
                            reason="r")  # eng already has a head
        self.k.org.create_dept("ops", "Operations")
        worker = self.k.org.hire(name="W", title="Op", dept="ops", job_description=JD, roles=["executor"],
                                 driver="fake", reason="r")
        self.assertIsNone(worker["reports_to"])
        head = self.k.org.hire(name="H", title="Head of Ops", dept="ops", job_description=JD, slot="lead",
                               driver="fake", reason="r")
        self.assertEqual(self.k.org.employee(worker["id"])["reports_to"], head["id"])
        self.assertEqual(head["reports_to"], self.ceo)
        self.assertEqual(head["stages"], ["shape", "approve", "retro"])


if __name__ == "__main__":
    unittest.main()


class SupervisorProtocol(KernelCase):
    """H6 (phase E): dispatch --prepare hands an external supervisor everything it needs to run
    the body itself; run finish salvages the result and runs what follows. One protocol for any
    supervisor; the claim, lease and concurrency rules are exactly push mode's (CONTRACT §12)."""

    def _prepared(self):
        t = self.add(dept="eng", task_class="demo")
        self.plan_and_approve(t, assignee=self.dev1, writes=[])
        p = self.k.prepare_dispatch(self.dev1, "execute")
        self.assertEqual(p["stage"], "execute")
        return t, p

    def test_prepare_writes_the_run_without_spawning(self):
        t, p = self._prepared()
        run_dir = self.home.run_dir(p["run"])
        self.assertTrue((run_dir / "brief.json").exists())
        self.assertEqual(json.loads((run_dir / "policy.json").read_text())["posture"], "act")
        self.assertIn("HOLON_RUN_TOKEN", p["env"])
        self.assertIn("HOLON_RESULT_PATH", p["env"])
        self.assertEqual(p["env"]["HOLON_RUN_DIR"], str(run_dir),
                         "the run directory's name (H-01): policy.json, trajectory.json, usage.json")
        self.assertEqual(p["env"]["HOLON_BRIEF_DIR"], str(run_dir), "the alias keeps working")
        self.assertTrue(p["command"])
        self.assertIn(("execute", "claimed"), self.pipeline_of(t))

    def test_finish_salvages_the_result_and_runs_what_follows(self):
        t, p = self._prepared()
        (self.home.run_dir(p["run"]) / "result.json").write_text(json.dumps(
            {"outcome": "success", "summary": "s", "documentation": "d",
             "usage": {"tokens_in": 10, "tokens_out": 5}}))
        out = self.k.finish_run(p["run"], 0)
        self.assertFalse(out.get("breach"))
        self.assertEqual(self.pipeline_of(t)[-1], ("verify", "ready"))
        self.assertEqual(self.k.work.get_run(p["run"])["tokens_in"], 10, "the envelope's usage")
        with self.assertRaises(TransitionError):  # nothing to finish twice: the result is in
            self.k.finish_run(p["run"], 0)

    def test_finish_without_a_result_is_a_breach(self):
        t, p = self._prepared()
        out = self.k.finish_run(p["run"], 1)
        self.assertTrue(out["breach"])
        self.assertEqual(self.k.work.last_execute_output(t["id"])["body"]["outcome"], "partial")


class HeartbeatFoundation(KernelCase):
    """The heartbeat is the kernel running unattended: `holon tick` on a timer. Every invariant
    below is a way the machine can silently stop doing its job, so each is proved directly.

    Foundation = the tick does not lose work, does not pile work up, and says so when it cannot."""

    def _due(self, cron="* * * * *"):
        w = self.add("scheduled digest", dept="eng")
        plan = self.plan(w, writes=[], assignee=self.dev1)
        self.k.approve(self.k.work.needs(plan["id"])[0], note="as a template", recurring=True)
        plan_id = self.k.work.needs(plan["id"])[0]
        s = self.k.schedules.add("recurring", cron, template_id=plan_id)
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        return s

    def test_a_due_schedule_fires_once_and_advances_its_clock(self):
        s = self._due()
        fired = self.k.tick()["fired"]
        self.assertEqual(len(fired), 1, f"one fire per tick: {fired}")
        row = self.k.schedules.get(s["id"])
        self.assertTrue(row["enabled"], "a healthy schedule stays enabled")
        self.assertNotEqual(row["next_run_at"], "2000-01-01T00:00:00.000000Z", "its clock advanced")
        self.assertEqual(self.k.tick()["fired"], [], "and it does not fire again until due")

    def test_a_failing_schedule_opens_one_incident_and_says_so(self):
        """A schedule that keeps failing is a broken harness, not slow work: retrying forever is
        exactly the failure the first real VPS heartbeat hit. `record_outcome` counts the streak and
        `_incidents` turns it into work the Chair can see."""
        s = self._due()
        for _ in range(3):
            self.k.schedules.record_outcome(s["id"], False)
        self.assertEqual(self.k.schedules.get(s["id"])["failure_streak"], 3)
        opened = self.k.tick()["triggered"]
        incidents = [i for i in opened if "incident" in i]
        self.assertTrue(incidents, f"an incident is opened: {opened}")
        title = self.k.work.get(incidents[0]["incident"])["title"]
        self.assertIn("keeps failing", title)
        # one incident, not one per tick: the next tick finds it already open
        self.assertEqual([x for x in self.k.tick()["triggered"] if "incident" in x], [],
                         "not a second one")

    def test_a_recovering_schedule_resets_its_streak(self):
        s = self._due()
        for _ in range(3):
            self.k.schedules.record_outcome(s["id"], False)
        self.k.schedules.record_outcome(s["id"], True)
        self.assertEqual(self.k.schedules.get(s["id"])["failure_streak"], 0,
                         "a success forgives the streak, so a flapping schedule does not accumulate")

    def test_the_tick_is_idempotent(self):
        """Two ticks in a row must not double-fire, double-alarm, or double-count: the heartbeat
        runs every two minutes and cannot know whether the last one finished."""
        self._due()
        first, second = self.k.tick(), self.k.tick()
        self.assertEqual(len(first["fired"]), 1)
        self.assertEqual(second["fired"], [], "the second tick is a no-op")

    def test_a_disabled_schedule_never_fires(self):
        """`enabled=0` is an import's landing state (an import is not an approval). A disabled
        schedule must be inert, not merely overdue."""
        s = self._due()
        self.k.wconn.execute("UPDATE schedules SET enabled=0 WHERE id=?", (s["id"],))
        self.assertEqual(self.k.tick()["fired"], [])

    def test_a_cancelled_template_stops_firing_and_says_disabled(self):
        w = self.add("scheduled digest", dept="eng")
        plan = self.plan(w, writes=[], assignee=self.dev1)
        self.k.approve(self.k.work.needs(plan["id"])[0], note="as a template", recurring=True)
        plan_id = self.k.work.needs(plan["id"])[0]
        s = self.k.schedules.add("recurring", "* * * * *", template_id=plan_id)
        self.k.work.cancel(w["id"], "no longer wanted")
        self.k.wconn.execute("UPDATE schedules SET next_run_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (s["id"],))
        fired = self.k.tick()["fired"]
        self.assertIn("disabled", str(fired), f"the cancel is reported: {fired}")

    def test_the_tick_reports_what_it_swept_without_losing_the_work(self):
        """A lease that expired is recovered by the sweep and its stage returns to the board, so a
        body can take it again — never deleted, never silently orphaned (the first VPS wave found
        two bodies contending for one result file)."""
        t = self.add(dept="eng", task_class="demo")
        plan = self.plan(t, writes=[], assignee=self.dev1)
        self.k.approve(plan["id"])
        c = self.k.claim("execute", self.dev1)
        self.k.wconn.execute("UPDATE runs SET lease_until='2000-01-01T00:00:00.000000Z', "
                             "heartbeat_at='2000-01-01T00:00:00.000000Z' WHERE id=?", (c.run["id"],))
        swept = self.k.tick()["swept"]
        self.assertEqual(len(swept), 1, f"the expired lease is recovered: {swept}")
        self.assertEqual(self.pipeline_of(t)[-1][1], "ready", "the stage is back on the board")
        self.assertIsNotNone(self.k.claim("execute", self.dev1), "and the work is claimable again")

    def test_the_heartbeat_backs_up_and_reports_it(self):
        self.k.config["healing"]["backup_every_h"] = 24
        self.k.wconn.execute("INSERT OR REPLACE INTO settings VALUES ('backup_ts', '2000-01-01T00:00:00.000000Z')")
        took = [x for x in self.k.tick()["triggered"] if "backup" in x]
        self.assertEqual(len(took), 1, f"the backup is reported: {took}")
        self.assertTrue((self.home.root / "backups").is_dir(), "and it exists")


class TheChairsInbox(LaneCase):
    """The Chair's inbox is the only channel from the organization to its owner. A run that found
    something the Chair must decide, a stage nobody can claim, a red-team breach — all of them are
    surfaced by `notify()`, which shells `[notify].command` and treats the exit code as the proof
    of delivery.

    The first live instance ran `operant org holon notify` with no Chair channels configured. That
    command prints "no [chair] channels configured — the message has nobody to reach" and **exits
    0**, because it considers "no channels" a successful no-op. The kernel therefore advanced its
    `notify_seq` cursor past the owed items and recorded `notify: sent`, so the digest was marked
    delivered, was never retried, and the Chair never heard about a lane breach or an incident.
    Nothing in the journal says the message went nowhere."""

    def _owe_the_chair(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        self.k.submit(c.run["id"], self.exec_env(writes=["/elsewhere/x.txt"], external_effects=["emailed a client"]))
        return self.k.pending_for_chair(0)

    def test_a_command_that_drops_the_message_and_exits_zero_is_a_failed_send(self):
        owed = self._owe_the_chair()
        self.assertTrue(owed, "the Chair is owed an item")
        self.k.config["notify"] = {"command": "echo 'no [chair] channels configured'",
                                    "dropped_marker": "no [chair] channels configured"}
        out = self.k.notify()
        self.assertFalse(out["sent"], "a command that printed 'nobody to reach' did not deliver")
        still = self.k.pending_for_chair(int(self.k._setting("notify_seq") or 0))
        self.assertTrue(still, f"the item stays owed: {still}")
        ev = self.k.work.conn.execute(
            "SELECT to_status, note FROM events WHERE entity='notify' ORDER BY seq DESC LIMIT 1").fetchone()
        self.assertEqual(ev[0], "failed", "and the journal says so")

    def test_a_command_that_exits_zero_having_delivered_is_recorded_as_sent(self):
        owed = self._owe_the_chair()
        self.assertTrue(owed, "the Chair is owed an item")
        self.k.config["notify"] = {"command": "true", "dropped_marker": "no [chair] channels"}
        out = self.k.notify()
        self.assertTrue(out["sent"])
        self.assertFalse(self.k.pending_for_chair(int(self.k._setting("notify_seq") or 0)))

    def test_a_command_that_fails_honestly_leaves_the_items_owed(self):
        # the pre-existing behaviour must survive the fix: OS-5, a failed send retries
        owed = self._owe_the_chair()
        self.assertTrue(owed, "the Chair is owed an item")
        self.k.config["notify"] = {"command": "false"}
        self.assertFalse(self.k.notify()["sent"])
        self.assertTrue(self.k.pending_for_chair(int(self.k._setting("notify_seq") or 0)))

    def test_a_quiet_instance_notifies_nobody_and_moves_nothing_owed(self):
        self.k.config["notify"] = {"command": "echo 'nothing'"}
        before = self.k.pending_for_chair(0)
        self.assertIsNone(self.k.notify(), "nothing owed means nothing is sent")
        self.assertEqual(self.k.pending_for_chair(0), [])
        self.assertEqual(before, [], f"nothing was owed before and nothing was sent: {before}")


class TheChairsInboxReachesIt(LaneCase):
    """The Chair's digest is the only thing the organization can say to its owner, and it read
    `events JOIN tasks` and nothing else — so a message was invisible to it. An employee who
    needed a key, or who found the work blocked, posted into `messages` and the Chair never
    learned it: `pending_for_chair(0)` came back empty with the message sitting unread in the
    table, and nothing anywhere reported the gap.

    A question to the Chair is now a digest line, and a delivered message stops being new by the
    same cursor discipline `dropped_marker` already relies on — not a second one."""

    def _post(self, to_ref, body, kind="question"):
        return self.k.work.post_message(f"emp:{self.dev1}", to_ref, body, kind=kind)

    def test_an_employees_question_to_the_chair_reaches_the_digest(self):
        self._post("chair", "the build needs a key, which one?")
        lines = self.k.pending_for_chair(0)
        self.assertTrue(any("key" in l for l in lines), f"the question is owed to the Chair: {lines}")
        self.assertTrue(any(f"emp:{self.dev1}" in l for l in lines),
                        "and it names who asked, not just what")

    def test_a_request_for_work_the_chair_must_decide_reaches_the_digest(self):
        self._post("chair", "please approve reaching outside the org", kind="request")
        self.assertTrue(self.k.pending_for_chair(0), "a request is owed to the Chair")

    def test_a_message_to_a_colleague_is_not_the_chairs_business(self):
        self._post(f"emp:{self.dev2}", "can you look at this?", kind="dm")
        self.assertEqual(self.k.pending_for_chair(0), [], "a colleague's dm is not a digest line")

    def test_a_dm_to_the_chair_is_not_work_the_kernel_chases(self):
        """A `dm` addressed to the Chair is a human writing to a human. Chasing it would put a
        person's aside in the same digest as a stage nobody can claim, which is how a digest
        stops being opened. `question` and `request` ask something of the Chair; a `dm` does not."""
        self._post("chair", "thanks, that was useful", kind="dm")
        self.assertEqual(self.k.pending_for_chair(0), [], "a dm to the Chair is not owed")
        self._post("chair", "but which key?", kind="question")
        self.assertTrue(self.k.pending_for_chair(0), "a question still is")

    def test_a_delivered_message_stops_being_new(self):
        self._post("chair", "which key?")
        before = self.k.pending_for_chair(0)
        self.assertTrue(before, "the question is owed")
        self.k.config["notify"] = {"command": "true", "dropped_marker": "no channels"}
        self.assertTrue(self.k.notify()["sent"], "the digest was sent")
        self.assertFalse(self.k.pending_for_chair(int(self.k._setting("notify_seq") or 0)),
                         "a delivered message is no longer new")

    def test_an_undelivered_message_stays_owed(self):
        self._post("chair", "which key?")
        self.assertTrue(self.k.pending_for_chair(0))
        self.k.config["notify"] = {"command": "false"}
        self.assertFalse(self.k.notify()["sent"])
        self.assertTrue(self.k.pending_for_chair(int(self.k._setting("notify_seq") or 0)),
                        "a failed send leaves the question owed")


class DigestRows(LaneCase):
    """The digest a chat channel renders (2026-10-23): `pending_for_chair` returns prose, so a
    button could only ever carry a copy of that prose — no stable id, no verb. The structured
    rows must therefore ride ALONGSIDE the prose and never replace it: the live instance's
    `[notify] command` is a plaintext shell, so a structured-only digest would break delivery
    the moment it shipped. Additivity is the property that keeps both surfaces alive at once."""

    def _owe(self):
        w = self.add(risk="low", task_class="c", lane="fast", dept="eng")
        c = self.k.claim("execute", self.dev1, task_id=w["id"])
        self.k.submit(c.run["id"],
                      self.exec_env(writes=["/elsewhere/x.txt"], external_effects=["emailed a client"]))
        return w

    def test_the_digest_still_returns_prose_for_a_plaintext_command(self):
        """The contract the live adapter depends on: `pending_for_chair` is a list of strings."""
        self._owe()
        lines = self.k.pending_for_chair(0)
        self.assertTrue(lines, "something is owed")
        for line in lines:
            self.assertIsInstance(line, str)

    def test_every_prose_row_carries_a_structured_twin_with_a_stable_id(self):
        """A button needs an id and a verb, so each digest line gains a twin: the same text, plus
        `action`, `id` and `kind`. Where there is no natural id (a classifier-down notice) the
        twin still exists and says so rather than being dropped from the button surface."""
        self._owe()
        rows = self.k.digest_rows(0)
        self.assertTrue(rows, "structured rows exist")
        by_text = {r["text"]: r for r in rows}
        for line in self.k.pending_for_chair(0):
            self.assertIn(line, by_text, f"the prose row {line!r} has no structured twin")
        for r in rows:
            self.assertIn("action", r)
            self.assertIn("kind", r)
            self.assertIn("text", r)

    def test_a_row_that_names_a_message_id_is_pressable(self):
        """The live instance, 2026-10-23: the owed question's prose ends `(holon msg thread
        msg_01M5K… )`, and the id contains an uppercase letter. A lowercase-only id class dropped the
        action from every such row — a button that cannot answer an employee's question is exactly
        the gap this whole surface exists to close, and it would have failed silently.
        `msg thread` is wrong for a reply too: the twin names `msg reply`, the one that answers."""
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "which key should I use?",
                                      kind="question")
        rows = {r["id"]: r for r in self.k.digest_rows(0) if r["id"]}
        self.assertIn(ask, rows, f"the owed question carries its id: {list(rows)}")
        self.assertEqual(rows[ask]["kind"], "question")
        self.assertTrue(rows[ask]["action"].startswith("msg "), rows[ask]["action"])

    def test_the_structured_twin_keeps_the_prose_order_and_dedupe(self):
        """The twin must not be a second hand-written list: it is derived from the same rows the
        prose came from, so it cannot drift from what the Chair actually reads — same order, same
        dedupe, same count."""
        self._owe()
        rows = self.k.digest_rows(0)
        self.assertEqual([r["text"] for r in rows],
                         list(dict.fromkeys(self.k.pending_for_chair(0))),
                         "one structured row per prose line, in the same order")

class TheChairsInboxAnswers(LaneCase):
    """A digest the Chair cannot answer is a broadcast. `holon msg` offered send, inbox and
    request and no reply, and `thread_id` was a column no path in the kernel ever wrote — so a
    conversation was not representable: every message opened its own thread, and the answer to a
    question was indistinguishable from a new remark about something else.

    `msg reply` posts into the thread it answers and `msg thread` reads that thread in order.
    No schema change: the column exists, and this is its first caller."""

    def test_a_reply_joins_the_thread_it_answers(self):
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "which key?", kind="question")
        reply = self.k.work.post_message("chair", f"emp:{self.dev1}", "the staging key",
                                          kind="answer", thread_id=ask)
        rows = self.k.work.messages(to_refs=["chair"], limit=10)
        by_id = {m["id"]: m for m in rows}
        self.assertEqual(by_id[ask]["thread_id"], by_id[reply]["thread_id"],
                         "the answer reads in the thread it answers")

    def test_an_unrelated_message_starts_its_own_thread(self):
        a = self.k.work.post_message(f"emp:{self.dev1}", "chair", "first", kind="question")
        b = self.k.work.post_message(f"emp:{self.dev2}", "chair", "unrelated", kind="question")
        self.assertNotEqual(self.k.work.messages(to_refs=["chair"], limit=10)[0]["thread_id"],
                            self.k.work.messages(to_refs=["chair"], limit=10)[1]["thread_id"],
                            "two questions are two threads")

    def test_a_thread_reads_as_a_conversation(self):
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "which key?", kind="question")
        self.k.work.post_message("chair", f"emp:{self.dev1}", "the staging key",
                                 kind="answer", thread_id=ask)
        thread = self.k.work.thread(ask)
        self.assertEqual([m["body"] for m in thread], ["which key?", "the staging key"],
                         "the thread is the question and its answer, in order")

    def test_a_question_the_chair_answered_is_no_longer_owed(self):
        """The digest owes a question until the Chair answers it; a reply acks it, so the same
        line is not re-sent on every heartbeat until something else advances the cursor."""
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "which key?", kind="question")
        self.assertTrue(self.k.pending_for_chair(0), "the question is owed")
        self.k.work.reply(ask, "chair", "the staging key")
        self.assertFalse(self.k.pending_for_chair(0), "answered, so nothing is owed")
        thread = self.k.work.thread(ask)
        self.assertEqual([m["body"] for m in thread], ["which key?", "the staging key"],
                         "the answer reads inside the question's thread")


class EveryMessageNamesWhoSpoke(LaneCase):
    """The channel's only real asset is that a message's sender is the kernel's assertion, not the
    body's claim. `cmd_msg_send` passes `_sender(args, k)`, which resolves the authenticated
    principal, and `--as <id>` is refused when it disagrees with it.

    That is the law this class asserts rather than assumes: if a body could claim a colleague's
    identity, the digest would name the wrong employee and the Chair would answer the wrong one.
    The adapter is told to read `messages.sender` and `messages.task_id` — the two links that
    exist — rather than a platform-side identity."""

    def test_a_message_carries_the_sender_the_kernel_resolved(self):
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "which key?", kind="question")
        m = self.k.work.messages(to_refs=["chair"], limit=1)[0]
        self.assertEqual(m["sender"], f"emp:{self.dev1}", "the sender is the authenticated employee")
        self.assertEqual(m["id"], ask)

    def test_a_reply_names_its_own_sender_not_the_one_answered(self):
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "which key?", kind="question")
        self.k.work.reply(ask, "chair", "the staging key")
        thread = self.k.work.thread(ask)
        self.assertEqual(thread[0]["sender"], f"emp:{self.dev1}", "who asked")
        self.assertEqual(thread[1]["sender"], "chair", "who answered — the kernel's assertion")

    def test_a_message_belongs_to_the_work_it_is_about(self):
        w = self.add("Build report", dept="eng", task_class="ops", risk="low")
        mid = self.k.work.post_message(f"emp:{self.dev1}", "chair", "this task is blocked",
                                       kind="question", task_id=w["id"])
        got = [m for m in self.k.work.messages(to_refs=["chair"], limit=10) if m["id"] == mid][0]
        self.assertEqual(got["task_id"], w["id"], "the message names the work it is about")

    def test_a_reply_carries_the_answered_messages_task(self):
        w = self.add("Build report", dept="eng", task_class="ops", risk="low")
        ask = self.k.work.post_message(f"emp:{self.dev1}", "chair", "blocked", kind="question",
                                       task_id=w["id"])
        self.k.work.reply(ask, "chair", "do it this way")
        thread = self.k.work.thread(ask)
        self.assertEqual([m["task_id"] for m in thread], [w["id"], w["id"]],
                         "the answer stays attached to the work the question was about")

    def test_the_chair_reaches_a_reply_and_an_employee_reaches_one(self):
        """`msg reply` is the one command both directions need: an employee answering a colleague,
        and the Chair answering a body. Actored `employee`, it reaches an MCP-mounted body through
        the derived fence, and the Chair reaches it because the Chair may do anything."""
        from holon.cli import REGISTRY
        reply = [c for c in REGISTRY if c.path == ("msg", "reply")][0]
        self.assertEqual(reply.actor, "employee")
        from holon.mcp import agent_commands
        self.assertIn(("msg", "reply"), agent_commands(),
                      "a mounted body may answer a colleague")


class TheChannelIsReachableFromTheCli(LaneCase):
    """W1 and W2 are only real if the commands that reach them exist. `msg send` had no way to
    name a `question` or a `request` — it posted a `dm`, the one kind the digest does not chase —
    so the whole employee → Chair path was unreachable from the CLI while the kernel supported
    it. And a Chair that named itself with `--as chair` produced `emp:chair`, a sender no employee
    key resolves to, which every later reader would treat as a body.

    Measured on a real instance: ask with `--kind question`, answer without `--as`, and the
    conversation lands as `emp:<id> → chair` [question, acked] and `chair → emp:<id>` [answer]."""

    def _cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = cli.main(["--home", str(self.home.root), "--json", *argv])
        text = out.getvalue().strip()
        return rc, (json.loads(text) if text else None), err.getvalue()

    def test_msg_send_can_name_the_kinds_the_digest_chases(self):
        from holon.cli import REGISTRY
        send = [c for c in REGISTRY if c.path == ("msg", "send")][0]
        kinds = next((dict(kw).get("choices") for a, kw in send.args if "--kind" in a), None)
        self.assertIn("question", kinds or [], "a question is postable from the CLI")
        self.assertIn("request", kinds or [], "so is a request")
        # and it posts as that kind, not merely offers it: the whole employee → Chair path was
        # unreachable while `--kind` was absent, because `dm` is the one kind the digest does
        # not chase.
        rc, out, _ = self._cli("msg", "send", "chair", "which key?", "--kind", "question",
                              "--as", "emp_emp_x", "--literal")
        self.assertEqual(rc, 0, out)
        row = self.k.work.messages(to_refs=["chair"], limit=1)[0]
        self.assertEqual(row["kind"], "question", f"it posted as a question: {row['kind']}")
        self.assertTrue(self.k.pending_for_chair(0), "and the digest owes it")

    def test_a_reply_goes_back_to_whoever_asked(self):
        """The reply's recipient is the answered message's *sender*, not its recipient: an
        employee asking the Chair is answered to `emp:<id>`, and a body asking a colleague is
        answered back to that colleague, so the conversation is reachable from either inbox."""
        rc, ask, _ = self._cli("msg", "send", "chair", "which key?", "--kind", "question",
                                "--as", "emp_emp_x", "--literal")
        self.assertEqual(rc, 0, ask)
        rc, reply, _ = self._cli("msg", "reply", ask["id"], "the staging key", "--literal")
        self.assertEqual(rc, 0, reply)
        thread = self.k.work.thread(ask["id"])
        self.assertEqual([m["sender"] for m in thread], ["emp:emp_emp_x", "chair"],
                         "the thread names both parties the way the kernel resolved them")
        self.assertEqual([m["to_ref"] for m in thread], ["chair", "emp:emp_emp_x"],
                         "the answer goes back to whoever asked")

    def test_the_chair_naming_itself_as_an_employee_is_refused(self):
        """`--as chair` from the Chair is not an employee id: it produced `emp:chair`, which no
        employee key resolves to and which every later reader would treat as a body."""
        rc, ask, _ = self._cli("msg", "send", "emp:emp_x", "which key?", "--kind", "question", "--literal")
        self.assertEqual(rc, 0, ask)
        rc, _, err = self._cli("msg", "reply", ask["id"], "x", "--as", "chair", "--literal")
        self.assertEqual(rc, FORBIDDEN, "the Chair naming itself as an employee is refused")
        self.assertIn("not an employee", err)
