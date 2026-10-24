"""The bylaws layer (U1–U3, K-4/K-5): units declared in manifests, synced into the database,
published as read API v3, pooled into briefs and reach, consumed through typed requests."""

import io
import json
import os
import sys
import time
import types
from contextlib import redirect_stdout
from pathlib import Path

from holon.execution.service import Kernel
from holon.core.db import now
from holon.bylaws import units
from holon.integrity import doctor, lint
from tests.helpers import JD, KernelCase

VAULT = """
charter = "the research substrate"
category = "service"
tier = "foundation"
roots = ["%s/data/vault"]
pool_of = ["ops"]
consumed = []

[[services]]
id = "kosmos"
command = "kosmos query"
effect = "read"
health = "kosmos --version"
accepts = "a research query"
returns = "sources and notes"
"""

BROWSER = """
charter = "the web and browser surface"
category = "surface"
pool_of = ["*"]
owner = "platform-ops"

[[services]]
id = "sourcehound"
command = "sourcehound $GROUP"
args = "sourcehound <web|news|reddit|research|twitter|youtube|finance|patents> [<verb>] [--json]"
output = "json"
effect = "read"
note = "prefer for any body: the native web_* tools are always loaded; CLI harnesses run the sourcehound CLI"
health = "cloakctl doctor --json"

[[services]]
id = "browseros-neo"
command = "/usr/bin/browseros-neo"
accepts = "a page that needs a real local browser"
returns = "text"
effect = "write"
note = "the apply engine's tier: anti-bot pages, at BROWSER_CDP_URL"
health = "browseros-neo --version"
"""

SURFACE_INCOMPLETE = """
charter = "a surface nobody can use"
category = "surface"
pool_of = ["*"]

[[services]]
id = "thing"
command = "thing"
accepts = "x"
returns = "y"
"""


class Bylaws(KernelCase):
    # ops pools the vault and has a terminal fence ([scope.dept.ops] commands): the fence exists,
    # so the pooled services' entry points join it and lint's bylaws pack stays quiet. The
    # subclass below removes the fence to test the other side of the rule.
    config = '[scope.dept.ops]\ncommands = ["git"]\n'

    def setUp(self):
        super().setUp()
        o = self.k.org
        o.create_dept("ops", "Operations", "Run things")
        self.maker = o.hire(name="Mo", title="Ops engineer", dept="ops", job_description=JD, slot="maker",
                            driver="fake", reason="r", status="active")["id"]
        self.checker = o.hire(name="Ce", title="Ops checker", dept="ops", job_description=JD, slot="checker",
                              driver="fake", reason="r", status="active")["id"]
        d = self.home.bylaws_units_dir
        d.mkdir(parents=True)
        (d / "vault.toml").write_text(VAULT % self.tmp)

    def _sync(self):
        return units.sync(self.k)

    def configure(self, toml):
        """Replace the instance's [scope] config wholesale (KernelCase's class config included)."""
        self.home.config_file.write_text(self.pipeline + "\n" + toml)
        self.k.close()
        self.k = Kernel(self.home)

    def test_sync_publishes_units_and_services_as_read_api_v4(self):
        out = self._sync()
        self.assertEqual((out["units"], out["services"]), (1, 1))
        self.assertEqual(self.k.wconn.execute(
            "SELECT value FROM api_meta WHERE key='api_version'").fetchone()[0], "4")
        self.assertEqual([r["slug"] for r in self.k.wconn.execute("SELECT * FROM api_units")], ["vault"])
        self.assertEqual([r["unit"] for r in self.k.wconn.execute("SELECT * FROM api_services")], ["vault"])
        self.assertEqual(self.k.wconn.execute(
            "SELECT command FROM api_services WHERE unit='vault' AND service='kosmos'").fetchone()[0],
            "kosmos query")
        self.assertEqual([f for f in lint.run(self.k, ["api", "bylaws"])
                         if f["rule"] != "bylaws.command"], [],
                       "the fixture declares a tool (kosmos) this machine has not installed, "
                       "which Fix 2 correctly reports; every other finding must stay empty")

    def test_api_memory_reads_the_live_memory(self):
        self.k.memory.add_item("org", "decision", "v3 is the bylaws API", "this test")
        self._sync()
        rows = self.k.mconn.execute("SELECT scope, kind, content FROM api_memory").fetchall()
        self.assertTrue(any(r["content"] == "v3 is the bylaws API" for r in rows),
                        "decisions are readable on the memory database, where the view lives")

    def test_bind_service_and_bind_pool_catch_dangling_references(self):
        (self.home.bylaws_units_dir / "vault.toml").write_text((VAULT % self.tmp).replace(
            'consumed = []', 'consumed = ["ghost:nothing"]').replace('pool_of = ["ops"]', 'pool_of = ["no-such-dept"]'))
        findings = lint.run(self.k, ["bylaws"])
        msgs = {f["message"] for f in findings}
        self.assertTrue(any("bind.service" in m for m in msgs), msgs)
        self.assertTrue(any("bind.pool" in m for m in msgs), msgs)
        self._sync()   # the source is the source: sync still lands parse-valid rows; lint is the guard
        self.assertEqual(self.k.wconn.execute(
            "SELECT other FROM unit_edges WHERE kind='consumes' AND slug='vault'"
        ).fetchone()[0], "ghost:nothing", "the dangling edge is visible until fixed")

    def test_pooled_units_join_the_reach_and_render_in_briefs(self):
        self._sync()
        vault = str(self.tmp / "data" / "vault")
        s = self.k.scope("ops")
        self.assertIn(vault, s["read_roots"], "the pooled unit's roots join the reach as reads (U2)")
        self.assertNotIn(vault, s["roots"], "never the pen: a pooled root is not writable reach (BYLAWS §14)")
        self.assertEqual(s["commands"], ["git", "kosmos"],
                         "a fence that exists names the pooled services' entry points (R-05)")
        w = self.add(dept="ops", task_class="demo")
        self.plan_and_approve(w, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        ctx = (c.brief_dir / "context.md").read_text()
        self.assertIn("Capabilities", ctx, "presence: the executor sees what it can call (U2)")
        self.assertIn("vault:kosmos", ctx)
        self.assertIn("msg request", ctx, "writes to a pooled unit go through a request, never the pen")
        self.assertIn("You may read (never write)", (c.brief_dir / "BRIEF.md").read_text(),
                      "the brief lists a pooled root as a read, not a place to work")
        w2 = self.add("fresh plan", dept="ops", task_class="demo")
        pc = self.k.claim("plan", self.maker, task_id=w2["id"])
        self.assertIn("Capabilities", (pc.brief_dir / "context.md").read_text(),
                      "the planner chooses tools and grades knowing what its department pools (R-01)")

    def test_pooled_roots_are_read_only_in_policy_grading_and_isolation(self):
        from holon.execution import isolation
        self._sync()
        vault = self.tmp / "data" / "vault"
        vault.mkdir(parents=True)
        # grading: a write into a pooled root is work for the unit's owner — floor G3 (R-06)
        w = self.add(dept="ops", task_class="demo", risk="low")
        p = self.plan(w, planner=self.maker, writes=[str(vault / "x.txt")], grade="G0")
        self.assertEqual(p["grade"], 3, "a pooled root is never writable reach, so the write goes to the Chair")
        reasons = json.loads(self.k.work.stages(w["id"], "plan")[0]["grade_reasons"] or "[]")
        self.assertTrue(any("pooled unit" in r for r in reasons), reasons)
        # policy: read_roots, never roots — a runtime fencing writes by roots refuses it (R-06)
        w2 = self.add("policy probe", dept="ops", task_class="demo")
        self.plan_and_approve(w2, planner=self.maker, assignee=self.maker, writes=[])
        c = self.k.claim("execute", self.maker)
        pol = json.loads((c.brief_dir / "policy.json").read_text())
        self.assertIn(str(vault), pol["read_roots"])
        self.assertNotIn(str(vault), [str(r) for r in pol["roots"]])
        # isolation: the agent user is granted read on a pooled root, never write (R-06)
        r = isolation.reach(self.k)
        self.assertIn(str(vault.resolve()), [str(x) for x in r["reads"]])
        self.assertNotIn(str(vault.resolve()), [str(x) for x in r["writes"]])
    def test_a_department_that_pools_but_lists_no_commands_is_never_derived_a_fence(self):
        """Pooling must not derive a fence out of nothing: an empty commands list means ungoverned,
        and switching it on would refuse every other command the department runs. Lint warns (R-05)."""
        self.configure("")
        self._sync()
        self.assertEqual(self.k.scope("ops")["commands"], [])
        found = lint.run(self.k, ["bylaws"])
        warned = [f for f in found if f["rule"] == "bylaws.fence"]
        self.assertTrue(any("ops" in f["where"] for f in warned), found)
        self.assertTrue(any("fence is open" in f["message"] for f in warned), found)

    def test_a_fence_that_exists_only_in_act_commands_still_names_the_pooled_services(self):
        """R-05's other half, found by red-team: an act fence is a fence. A department fenced
        only by act_commands ran its execute stages refusing the pooled services' entry points —
        the brief advertised `kosmos query` and the policy denied it. Only a fence that exists
        is extended; no observe fence is derived from pooling."""
        self.configure('[scope.dept.ops]\nact_commands = ["git"]\n')
        self._sync()
        s = self.k.scope("ops")
        self.assertEqual(s["commands"], [], "no observe fence is derived out of pooling (R-05)")
        self.assertEqual(s["act_commands"], ["git", "kosmos"],
                         "the act fence names the pooled services' entry points")

    def test_route_names_only_writable_reach_a_pooled_root_belongs_to_its_owner(self):
        """The R-06 pitfall, decided and pinned: `holon route` answers for the writable reach only —
        a pooled root is its unit owner's work, never a pooling department's, so writes to it go
        through msg request, not to the department `route` would name."""
        self._sync()
        from holon import cli
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--home", str(self.home.root), "--json", "route",
                           str(self.tmp / "data" / "vault" / "x")])
        self.assertEqual(rc, cli.NOTHING)
        self.assertIsNone(json.loads(buf.getvalue())["scope"])

    def test_a_typed_request_names_the_entry_point(self):
        self._sync()
        from holon import cli
        args = types.SimpleNamespace(as_=self.maker, dept="eng", title="Query the vault",
                                     intent="get sources", acceptance=None, priority=3,
                                     service="vault:kosmos", json=True,
                                     principal=types.SimpleNamespace(kind="employee",
                                                                     employee_id=self.maker))
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.cmd_msg_request(args, self.k)
        t = json.loads(buf.getvalue())
        self.assertTrue(t["title"].startswith("[vault:kosmos]"), t["title"])
        args.service = "vault:nope"
        with self.assertRaises(ValueError):
            with redirect_stdout(io.StringIO()):
                cli.cmd_msg_request(args, self.k)

    def test_emit_derives_the_registry_and_doctor_flags_staleness(self):
        self._sync()
        p = units.emit(self.k)
        self.assertIn("vault:kosmos", p.read_text())
        self.assertEqual([f for f in doctor.run(self.k) if f["check"].startswith("bylaws")], [])
        import os
        import time
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace("the research substrate", "the research substrate, revised"))
        os.utime(self.home.bylaws_units_dir / "vault.toml", (time.time() + 5, time.time() + 5))
        checks = {f["check"] for f in doctor.run(self.k)}
        self.assertIn("bylaws.stale", checks, "a manifest newer than the sync is a warning")

    def test_org_wide_surfaces_render_in_every_brief_with_prefer_notes_and_down_marks(self):
        """C1–C3: a surface unit (category="surface", pool_of=["*"]) publishes the v4 views,
        renders in every entitled brief — including department-less work (C-G4) — says which
        route to prefer, offers write routes to act stages only (R-03), and marks a route whose
        health probe failed as (down) so a planner routes around it before spending."""
        (self.home.bylaws_units_dir / "browser.toml").write_text(BROWSER)
        self._sync()
        self.assertEqual(self.k.wconn.execute(
            "SELECT value FROM api_meta WHERE key='api_version'").fetchone()[0], "4")
        rows = {r["service"]: r for r in self.k.wconn.execute(
            "SELECT * FROM api_services WHERE unit='browser'")}
        self.assertEqual(rows["sourcehound"]["effect"], "read")
        self.assertTrue(rows["browseros-neo"]["note"].startswith("prefer") or
                        rows["sourcehound"]["note"].startswith("prefer"))
        self.assertEqual(rows["sourcehound"]["health"], "cloakctl doctor --json")
        self.assertEqual(self.k.wconn.execute("SELECT dept FROM api_pool").fetchone()[0], "*")
        self.assertEqual([f for f in lint.run(self.k, ["api", "bylaws"])
                         if f["rule"] != "bylaws.command"], [],
                       "the fixture declares a tool (kosmos) this machine has not installed, "
                       "which Fix 2 correctly reports; every other finding must stay empty")
        # a plan (observe) sees the read route named to prefer, never the write one (R-03)
        w = self.add("choose a tool", dept="ops", task_class="demo")
        pc = self.k.claim("plan", self.maker, task_id=w["id"])
        ctx = (pc.brief_dir / "context.md").read_text()
        self.assertIn("Capabilities", ctx)
        self.assertIn("browser:sourcehound", ctx)
        self.assertIn("args: sourcehound <web|news|reddit|research|twitter|youtube|finance|patents>", ctx,
                      "a surface's contract renders from its v4 fields, not accepts/returns")
        self.assertNotIn("browser:browseros-neo", ctx, "an observe stage runs no write route")
        self.assertIn("prefer:", ctx)
        # an execute (act) sees both, and a failed health probe marks the route (down)
        approval = self.k.submit(pc.run["id"], self.plan_env(writes=[]))["approval"]
        self.k.approve(approval["id"])
        # department-less work sees the org-wide surface too (C-G4) — claimed through the
        # executive seat: org-level work is the Chair's own (a dept-less task has no dept claim)
        w2 = self.add("org-level research", dept=None, task_class="demo")
        pc2 = self.k.claim("plan", self.ceo, task_id=w2["id"])
        self.assertIsNotNone(pc2, "a plan run is claimable now that the maker is free")
        self.assertIn("browser:sourcehound", (pc2.brief_dir / "context.md").read_text())
        self.k.submit(pc2.run["id"], self.plan_env(writes=[]))
        self.k.auth._put("smoke", json.dumps({"ts": now(), "results": [
            {"dept": "platform-ops", "name": "browser:browseros-neo", "ok": False, "exit_code": 1,
             "critical": False, "seconds": 1, "reason": "probe", "cmd": "browseros-neo --version",
             "tail": ""}]}))
        c = self.k.claim("execute", self.maker)
        ex = (c.brief_dir / "context.md").read_text()
        self.assertIn("browser:browseros-neo", ex)
        self.assertIn("(down", ex, "a dead tier is marked, never silently offered")
        # doctor names the dead surface and who pools it (C3)
        found = [f for f in doctor.run(self.k) if f["check"] == "surface.down"]
        self.assertTrue(any("browser:browseros-neo" in f["message"] and "every department" in f["message"]
                            for f in found), found)

    def test_a_surface_service_without_effect_and_note_is_refused(self):
        """C1's lint rule: a surface service declares effect (which posture may take the route)
        and note (which route to prefer) — the two fields that make a surface usable, not just
        declared."""
        (self.home.bylaws_units_dir / "browser.toml").write_text(SURFACE_INCOMPLETE)
        self._sync()
        found = [f for f in lint.run(self.k, ["bylaws"]) if f["severity"] == "error"]
        self.assertTrue(any("effect" in f["message"] and "note" in f["message"] for f in found), found)

    def test_a_manifest_outside_the_manifest_directory_never_empties_the_capability_layer(self):
        """The layer was renamed genome->bylaws; a manifest left at the old path is invisible to
        the kernel, and `bylaws sync` used to empty the units tables with no error and no lint
        finding -- one command from losing the whole capability layer."""
        self._sync()
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM units").fetchone()[0], 1)
        # the manifest moves to the path the kernel no longer reads
        legacy = self.home.root / "genome" / "units"
        legacy.mkdir(parents=True)
        (self.home.bylaws_units_dir / "vault.toml").rename(legacy / "vault.toml")
        # the sync refuses, and the tables survive
        with self.assertRaises(ValueError) as cm:
            self._sync()
        self.assertIn("refusing to empty", str(cm.exception))
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM units").fetchone()[0], 1,
                         "the units tables were emptied by a sync that could not see the manifest")
        # and lint names the orphan before anything runs
        found = [f for f in lint.run(self.k, ["bylaws"]) if f["severity"] == "error"]
        self.assertTrue(any(f["rule"] == "bylaws.orphan" and "vault.toml" in f["where"] for f in found), found)
        # doctor names it too
        found = [f for f in doctor.run(self.k) if f["check"] == "bylaws.orphan"]
        self.assertTrue(any("vault.toml" in f["message"] for f in found), found)
        # moving it back restores the layer
        (legacy / "vault.toml").rename(self.home.bylaws_units_dir / "vault.toml")
        self._sync()
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM units").fetchone()[0], 1)

    def test_a_sync_never_empties_populated_tables_from_an_empty_manifest_directory(self):
        """Whatever the cause -- a manifest moved, a directory renamed, a mount that vanished --
        a sync that finds no manifest must not silently delete the synced capability layer."""
        self._sync()
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM units").fetchone()[0], 1)
        for f in self.home.bylaws_units_dir.glob("*.toml"):
            f.unlink()
        with self.assertRaises(ValueError) as cm:
            self._sync()
        self.assertIn("refusing to empty", str(cm.exception))
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM units").fetchone()[0], 1)
        self.assertEqual(self.k.wconn.execute("SELECT count(*) FROM unit_services").fetchone()[0], 1)


    def test_a_dead_unit_of_any_category_is_reported_not_only_a_dead_surface(self):
        """C3 reports a failing health probe — and it used to report it only for a
        `category = "surface"` unit, so every pooled foundation was invisible to the only
        mechanism that names a dead capability. Measured 2026-10-24 with a control clone that
        differed only in that field: the surface clone warned, the `service` original said
        nothing, same exit-127 command. A capability an agent is offered but cannot run is
        worse than one it never sees: the brief still lists it.
        """
        # the vault is `category = "service"`; its health probe fails
        self._sync()
        self.k.auth._put("smoke", json.dumps({"ts": now(), "results": [
            {"dept": None, "name": "vault:kosmos", "ok": False, "exit_code": 127,
             "critical": False, "seconds": 1, "reason": "probe", "cmd": "kosmos --version",
             "tail": "kosmos: not found"}]}))
        found = [f for f in doctor.run(self.k) if f["check"] in ("unit.down", "surface.down")]
        self.assertTrue(any("vault:kosmos" in f["message"] and "exit 127" in f["message"]
                            and "ops" in f["message"] for f in found),
                        f"a service-category unit with a failing health probe reported nothing: {found}")
        # the historical key is preserved for a surface, so operator tooling keeps working
        (self.home.bylaws_units_dir / "browser.toml").write_text(BROWSER)
        self._sync()
        self.k.auth._put("smoke", json.dumps({"ts": now(), "results": [
            {"dept": None, "name": "browser:browseros-neo", "ok": False, "exit_code": 1,
             "critical": False, "seconds": 1, "reason": "probe", "cmd": "browseros-neo --version",
             "tail": ""}]}))
        dead = [f for f in doctor.run(self.k) if f["check"] == "surface.down"]
        self.assertTrue(any("browser:browseros-neo" in f["message"] for f in dead), dead)

    def test_a_declared_command_that_resolves_to_nothing_is_reported(self):
        """Fix 2 of the capability-layer plan. `validate` required a service to HAVE a command and
        never checked the command resolves, so a manifest could declare a dead capability and pass
        sync, promote and lint clean — while every pooling department was offered a route it could
        not run. A warning, not an error: the tool may only be installed where the agent runs,
        and lint runs where the operator is.
        """
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"',
                                       'command = "definitely-not-an-installed-tool --query"'))
        self._sync()
        found = [f for f in lint.run(self.k, ["bylaws"]) if f["rule"] == "bylaws.command"]
        self.assertTrue(any("vault:kosmos" in f["where"]
                            and "definitely-not-an-installed-tool" in f["message"] for f in found), found)
        self.assertTrue(all(f["severity"] == "warning" for f in found), found)
        # an absolute path to a real executable resolves — for the command AND its health probe,
        # which is the same kind of promise. A dead health probe is a dead capability.
        here = Path(sys.executable)
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"', f'command = "{here} -c pass"'
                                       ).replace('health = "kosmos --version"',
                                                 f'health = "{here} --version"'))
        self._sync()
        self.assertEqual([f for f in lint.run(self.k, ["bylaws"]) if f["rule"] == "bylaws.command"], [],
                         "a command AND a health probe whose first token is an existing path resolve")

    def test_a_read_route_may_not_name_the_whole_mutation_surface(self):
        """Fix 5 of the capability-layer plan. `effect` declared a read while the command was
        the whole binary, and `effect` was read in exactly one behavioral place in the tree
        (`capabilities/context.py:312`, which hides a write route from an observe posture) —
        it never constrained the command. So a route offered as a read could carry the
        mutation verbs, and identity's write safety rested on the store's own CLI rather
        than on the capability layer. A read route must name the verb it invokes."""
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"', 'command = "kosmos"'))
        self._sync()
        found = [f for f in lint.run(self.k, ["bylaws"]) if f["severity"] == "error"]
        self.assertTrue(any("effect=read" in f["message"] and "bare 'kosmos'" in f["message"]
                            for f in found), found)
        # narrowed to a verb, the same service passes
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"', 'command = "kosmos query"'))
        self._sync()
        self.assertEqual([f for f in lint.run(self.k, ["bylaws"])
                          if "effect=read" in f["message"]], [],
                         "a read route narrowed to a verb was still refused")
        # a service that genuinely needs the whole binary declares write, and is then invisible
        # to an observe posture (context.py:312) rather than offered as a read
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"\neffect = "read"',
                                       'command = "kosmos"\neffect = "write"'))
        self._sync()
        self.assertEqual([f for f in lint.run(self.k, ["bylaws"])
                          if "effect=read" in f["message"]], [],
                         "a write-effect service was refused by the read rule")

    def test_a_read_route_may_name_a_verb_free_path(self):
        """The red-team that caught my own Fix 5 twice over.

        First defect: the rule counted tokens, so a read-effect route whose command is ONE path
        to a genuinely read-only binary was refused. There is no verb to name on a single-file
        command, and refusing it would have blocked every legitimate single-binary surface
        (`/usr/bin/browseros-neo` is one, already shipped in BROWSER).

        Second, and worse: counting tokens also ACCEPTED `kosmos --query`, because a flag looks
        like a verb if you only count arguments. `identity --version` reaches every verb
        `identity` has exactly as bare `identity` does. What matters is whether the invocation
        addresses one verb — the head plus a non-flag argument — not how many tokens there are.
        """
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"',
                                       'command = "%s"' % (sys.executable,)))
        self._sync()
        self.assertEqual([f for f in lint.run(self.k, ["bylaws"])
                          if "declares effect=read" in f["message"]], [],
                         "a read route naming one binary path was refused by the verb rule: "
                         "there is no verb to name on a single-file command")
        # the second defect's guard: a FLAG is not a verb. `kosmos --query` reaches every verb
        # the binary has, exactly as the bare name does, so it must still be refused.
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"',
                                       'command = "kosmos --query"'))
        self._sync()
        found = [f for f in lint.run(self.k, ["bylaws"]) if "declares effect=read" in f["message"]]
        self.assertTrue(any("bare 'kosmos --query'" in f["message"] for f in found), found)

    def test_the_reachable_command_question_does_not_depend_on_the_cwd(self):
        """The concrete cwd-flip, measured (2026-10-24). With `bin/tool` present in the repo root,
        the pre-fix lint form answered runnable from the repo root and not runnable from /tmp for
        the SAME manifest. Now one resolver answers both, and it answers FALSE, because a relative
        path is not something a unit on another machine can be assumed to mean."""
        root = self.tmp / "dept"
        (root / "bin").mkdir(parents=True)
        (root / "bin" / "tool").write_text("#!/bin/sh\n")
        here = os.getcwd()
        try:
            os.chdir(root)
            self.assertIsNone(units.resolve_token("bin/tool"))
            os.chdir("/tmp" if os.path.isdir("/tmp") else here)
            self.assertIsNone(units.resolve_token("bin/tool"),
                             "the answer for a relative command must not move with the cwd")
        finally:
            os.chdir(here)

    def test_a_declared_health_probe_is_run_by_something(self):
        """Attack 5 of the red-team against my own fixes: C3 (doctor.py) reads
        `smoke_results()` looking up `failed.get(f"{slug}:{service}")`, and `capabilities/context`
        marks a route `(down)` off the same name. Neither wrote it. Smoke checks came only from the
        instance config's `[smoke]` block, so a unit declaring `health = "kosmos --version"` was
        an operator told their probe was being watched while no duty ever ran it — the whole
        C3 fix was a consumer with no producer, exactly the failure mode I had just written up as
        Fix 4's problem.

        Now the producer exists: a declared health is emitted as a smoke check named
        `<unit>:<service>`, derived from the same manifest being checked. This test asserts the
        name reaches the check list, and a probe that fails reaches the doctor's warning."""
        self._sync()
        checks = self.k.smoke_checks()
        names = {c["name"]: c for c in checks}
        self.assertIn("vault:kosmos", names,
                      "a declared health probe never became a smoke check, so nothing runs it")
        self.assertEqual(names["vault:kosmos"]["cmd"].strip(), "kosmos --version")
        self.assertFalse(names["vault:kosmos"]["critical"],
                         "a dead capability must not be able to block an unrelated department")

    def test_a_read_route_whose_args_name_its_verbs_is_not_refused_for_a_bare_command(self):
        """Gap 0 of the remaining-gaps outline-plan, which is the false positive Fix 5 shipped.

        Measured on the live instance 2026-10-24T18:26Z: strict lint was 3 errors, and one of them
        was `bylaws.manifest` refusing `browser.toml`'s `sourcehound` -- a route declaring
        `effect = "read"` with the bare `sourcehound`. That is the same rule that correctly refused
        the bare `identity`, and it cannot tell the two apart from the command alone, because
        Measured: `identity --help` and `sourcehound --help` EACH list a subcommand group. A bare
        head is the refused form for `identity` (which carries `set`/`propose` behind every verb)
        and the necessary form for `sourcehound` (whose head is only a dispatcher).

        The evidence that separates them is already in the manifest: `args` documents the call
        shape, and a group entry point writes its verb set right there as an alternation. So the
        rule now asks the manifest what it already declares, and a bare name whose `args` is
        absent or names no verb set still fails -- which is what keeps this from becoming a hole.

        The `sourcehound` fixture in VAULT carries the verb set measured 2026-10-24T20:13Z from
        `sourcehound --help` (34 real groups). The live `browser.toml` had declared
        `<search|read|news|papers|social>`, and none of those four is a real group, so the
        exemption was passing on shape alone. The `kosmos` verbs below are synthetic -- no kosmos
        binary on this machine -- so this test pins the rule's SHAPE, and the live instance is
        what proves it on a real CLI. (The sync `f9fb731` reverted this fixture's correction once;
        it is re-applied here, which is why the fixture is named rather than assumed.)
        """
        # the shape that regressed the live instance: a bare group entry point with its verb set
        # documented in args. The kosmos verbs are synthetic (no kosmos binary here); the
        # sourcehound args in the VAULT fixture carry the real 34-group set.
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace(
                'command = "kosmos query"',
                'command = "kosmos"\nargs = "kosmos <query|notes|list|get> [--json]"'))
        self._sync()
        self.assertEqual([f for f in lint.run(self.k, ["bylaws"])
                          if "declares effect=read" in f["message"]], [],
                         "a bare command whose args document the verb set was refused: this is "
                         "the sourcehound false positive that regressed the live instance")
        # the control that keeps the rule: a bare name whose args name NO verb set still fails,
        # which is the original defect and must not reopen
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace('command = "kosmos query"', 'command = "kosmos"'))
        self._sync()
        self.assertTrue(any("declares effect=read" in f["message"]
                            for f in lint.run(self.k, ["bylaws"])),
                        "a bare command with no args, or args naming only flags, was allowed: the "
                        "verb-set exemption has become a hole")
        # a bracket of optional FLAGS is not a verb set, and must not be mistaken for one
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            (VAULT % self.tmp).replace(
                'command = "kosmos query"', 'command = "kosmos"\nargs = "kosmos [--json]"'))
        self._sync()
        self.assertTrue(any("declares effect=read" in f["message"]
                            for f in lint.run(self.k, ["bylaws"])),
                        "a bracket of optional flags was read as a verb set: `[--json]` has no "
                        "alternation and must not exempt the route")

    def test_a_pipe_in_prose_is_not_a_verb_set(self):
        """This rule's own red-team, and it narrows the exemption I had just written.

        My first pattern accepted an `|`-alternation anywhere in `args`, in any of three shapes.
        Measured against near-misses, that was a silent hole in the exact rule meant to prevent
        one: `pipe output to jq: use --json then | jq`, `--sep "|" for the field separator`, and
        `<query> (a|b as a regex)` all contain a pipe and none names a verb set, so a manifest
        could exempt a mutation surface by mentioning a pipe in prose.

        The exemption is now a BRACKETED alternation only — how a call shape writes a choice and
        how nothing else is written. An unbracketed `a | b` is refused, which costs a
        documentation style nobody uses and closes the hole. This test fails on the looser form."""
        holes = [
            "kosmos --help describes piping output to jq: use --json then | jq",
            "kosmos <query> (a|b is a regex, not a verb set)",
            "kosmos query | list",
        ]
        for args in holes:
            (self.home.bylaws_units_dir / "vault.toml").write_text(
                (VAULT % self.tmp).replace(
                    'command = "kosmos query"',
                    "command = \"kosmos\"\nargs = '" + args + "'"))
            self._sync()
            self.assertTrue(any("declares effect=read" in f["message"]
                                for f in lint.run(self.k, ["bylaws"])),
                            f"a pipe in prose exempted a bare command: {args!r}")
        # and the bracketed forms are the ones that do exempt
        for args in ('kosmos <query|notes|list> [--json]', 'kosmos [query|notes|list]'):
            (self.home.bylaws_units_dir / "vault.toml").write_text(
                (VAULT % self.tmp).replace(
                    'command = "kosmos query"', f'command = "kosmos"\nargs = "{args}"'))
            self._sync()
            self.assertEqual([f for f in lint.run(self.k, ["bylaws"])
                              if "declares effect=read" in f["message"]], [],
                             f"a bracketed verb set was refused: {args!r}")
