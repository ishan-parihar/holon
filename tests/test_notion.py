"""The Notion mirror (adapters/notion): schema, holarchy relations, Chair actions back, drift repair."""

import hashlib
import hmac
import importlib.util
import json
import queue
import re
import sys
from pathlib import Path

from tests.fake_notion import BOT, CHAIR, FakeNotion
from tests.helpers import KernelCase

spec = importlib.util.spec_from_file_location(
    "holon_notion", Path(__file__).resolve().parents[1] / "adapters" / "notion" / "holon_notion.py")
hn = importlib.util.module_from_spec(spec)
sys.modules["holon_notion"] = hn  # dataclasses look their module up
spec.loader.exec_module(hn)


class Schema(KernelCase):
    """The mirror's schema against SQLite's: complete, typed, every edge readable from both ends."""

    def columns(self, entity):
        table = hn.TABLES[entity]
        sql = self.k.wconn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
        cols = {r[1]: r for r in self.k.wconn.execute(f"PRAGMA table_info({table})")}
        enumerated = set(re.findall(r"\b(\w+)\s+(?:IN\s*\(|BETWEEN\b)", sql)) & set(cols)
        refs = {c for c in cols if re.search(rf"\b{c}\s+TEXT[^,]*REFERENCES", sql)} | ({"head_id", "approver"} & set(cols))
        return set(cols), enumerated, refs

    def test_every_column_is_mirrored_or_accounted_for(self):
        for e in hn.ENTITIES:
            cols, _, _ = self.columns(e.key)
            mirrored = {p.column for p in e.props if p.column} | {r.column for r in e.rels if r.column}
            skipped = set(hn.UNMIRRORED.get(e.key, {}))
            self.assertEqual(mirrored - cols, set(), f"{e.key}: mirrors columns that do not exist")
            self.assertEqual(skipped - cols, set(), f"{e.key}: UNMIRRORED names columns that do not exist")
            self.assertEqual(mirrored & skipped, set(), f"{e.key}: both mirrored and listed as unmirrored")
            self.assertEqual(cols - mirrored - skipped, set(), f"{e.key}: columns neither mirrored nor accounted for")

    def test_enumerations_are_selects_and_references_are_dual_relations(self):
        open_sets = {"outcome", "kind", "roles", "task_class", "task_classes", "driver", "profile"}  # vocab/instance
        for e in hn.ENTITIES:
            _, enumerated, refs = self.columns(e.key)
            for p in e.props:
                if p.column in enumerated | open_sets:
                    self.assertIn(p.kind, ("select", "multi_select", "checkbox"), f"{e.key}.{p.name} is a text slug")
                self.assertNotIn(p.column, refs - {"decided_by"}, f"{e.key}.{p.name}: a reference must be a relation")
            self.assertEqual(refs - {r.column for r in e.rels} - set(hn.UNMIRRORED.get(e.key, {})), set(),
                             f"{e.key}: a reference column is neither a relation nor accounted for")
            for r in e.rels:
                self.assertEqual(hn.rel_schema(r, {x.key: x.key for x in hn.ENTITIES})["relation"]["type"],
                                 "dual_property")


class NotionMirror(KernelCase):
    def setUp(self):
        super().setUp()
        self.fake = FakeNotion()
        self.sync = hn.Sync(self.home, hn.Notion("tok", self.fake.base, min_interval=0))
        self.sync.setup("parent-page", [CHAIR])
        self.ds = self.sync.cfg["data_sources"]

    def tearDown(self):
        self.sync.close()
        self.fake.close()
        super().tearDown()

    def page(self, hid, entity="work"):
        return self.fake.pages[self.sync._page_of(entity, hid)]

    def prop(self, hid, name, entity="work"):
        return hn.plain(self.page(hid, entity)["properties"].get(name))

    def rel(self, hid, name, entity="work"):
        return [r["id"] for r in self.page(hid, entity)["properties"].get(name, {}).get("relation", [])]

    def test_the_schema_is_generated_and_checks_clean(self):
        self.assertEqual(self.sync.check(), [])
        work = self.fake.ds[self.ds["work"]]["properties"]
        self.assertEqual(work["Parent"]["relation"]["data_source_id"], self.ds["work"])
        self.assertIn("Children", work, "Notion maintains the holarchy's reverse side")
        self.assertEqual({o["name"] for o in work["Stage"]["select"]["options"]}, set(hn.vocab.STAGES))
        self.assertIn("Employees", self.fake.ds[self.ds["departments"]]["properties"])

    def test_the_holarchy_arrives_with_every_relation_and_a_second_push_writes_nothing(self):
        w = self.add(dept="eng", task_class="greeting")
        approval = self.plan(w)
        self.sync.push()
        plan = self.k.work.latest(w["id"], "plan")
        self.assertEqual(self.rel(plan["id"], "Parent"), [self.sync._page_of("work", w["id"])])
        self.assertEqual(self.rel(approval["id"], "Needs"), [self.sync._page_of("work", plan["id"])])
        self.assertEqual(set(self.rel(w["id"], "Children")),
                         {self.sync._page_of("work", s["id"]) for s in self.k.work.stages(w["id"])})
        self.assertEqual(self.rel(w["id"], "Department"), [self.sync._page_of("departments", self.k.org.dept("eng")["id"])])
        self.assertEqual(self.prop(approval["id"], "Grade"), f"G{approval['grade']}")
        self.assertEqual(self.rel(self.hod, "Department", "employees"),
                         [self.sync._page_of("departments", self.k.org.dept("eng")["id"])])
        run = self.k.wconn.execute("SELECT * FROM runs").fetchone()
        self.assertEqual(self.rel(run["id"], "Stage", "runs"), [self.sync._page_of("work", plan["id"])])
        self.assertEqual(self.sync.push(), {"created": 0, "updated": 0})
        self.assertEqual(self.sync.verify(), [], "every value and both ends of every relation match SQLite")
        children = self.fake.pages[self.sync._page_of("work", w["id"])]["properties"]["Children"]["relation"]
        children.pop()  # an edge lost on one side, with no event to say so
        self.assertTrue(any("lacks it in Children" in p for p in self.sync.heal()))
        self.sync.push()
        self.assertEqual(self.sync.verify(), [], "heal re-pushed the forward side and Notion restored the reverse")

    def test_the_chair_approves_in_notion_and_the_organization_moves(self):
        w = self.add(dept="eng")
        approval = self.plan(w)
        self.sync.push()
        self.fake.human_edit(self.sync._page_of("work", approval["id"]),
                             {"Chair decision": {"select": {"name": "approve"}},
                              "Chair note": {"rich_text": [{"text": {"content": "go"}}]}})
        self.sync.reconcile()
        self.assertEqual(self.k.work.get(approval["id"])["outcome"], "approve")
        self.assertIn("[via Notion] go", self.k.work.get(approval["id"])["decision_note"])
        self.sync.push()
        ex = self.k.work.latest(w["id"], "execute")
        self.assertEqual(self.prop(ex["id"], "Status"), "ready")
        self.assertEqual(self.prop(approval["id"], "Chair decision"), "", "inputs are cleared once acted on")

    def test_someone_elses_decision_and_any_other_edit_are_reverted(self):
        w = self.add(dept="eng")
        approval = self.plan(w)
        self.sync.push()
        pid = self.sync._page_of("work", approval["id"])
        self.fake.human_edit(pid, {"Chair decision": {"select": {"name": "approve"}}}, user="person-intern")
        self.fake.human_edit(self.sync._page_of("work", w["id"]),
                             {"Status": {"select": {"name": "done"}}}, user=CHAIR)
        self.sync.reconcile()
        self.assertEqual(self.k.work.get(approval["id"])["status"], "ready", "only the Chair decides")
        self.sync.push()
        self.assertEqual(self.prop(w["id"], "Status"), "open", "Holon's truth wins over a hand edit")

    def test_trashed_pages_come_back_and_the_chair_can_add_work(self):
        w = self.add(dept="eng")
        self.sync.push()
        self.fake.trash(self.sync._page_of("work", w["id"]))
        eng = self.sync._page_of("departments", self.k.org.dept("eng")["id"])
        new_page = self.fake.human_create(self.ds["work"], {
            "Title": {"title": [{"text": {"content": "Write a haiku"}}]},
            "Intent": {"rich_text": [{"text": {"content": "about sqlite"}}]},
            "Department": {"relation": [{"id": eng}]}})
        out = self.sync.reconcile()
        self.assertGreaterEqual(out["restored"], 1)
        self.assertFalse(self.page(w["id"])["in_trash"])
        created = self.k.wconn.execute("SELECT * FROM tasks WHERE title='Write a haiku'").fetchone()
        self.assertEqual((created["intent"], created["dept_id"], created["source"]),
                         ("about sqlite", self.k.org.dept("eng")["id"], "chair"))
        self.sync.push()
        self.assertEqual(hn.plain(self.fake.pages[new_page]["properties"]["Holon ID"]), created["id"])

    def test_webhooks_are_verified_and_bots_ignored(self):
        inbox = queue.Queue()
        self.assertEqual(self.sync.webhook(b'{"verification_token": "secret_x"}', {}, inbox), (200, "token stored"))
        body = json.dumps({"type": "page.properties_updated", "authors": [{"id": CHAIR, "type": "person"}],
                           "entity": {"id": "p1", "type": "page"}}).encode()
        sig = "sha256=" + hmac.new(b"secret_x", body, hashlib.sha256).hexdigest()
        self.assertEqual(self.sync.webhook(body, {"X-Notion-Signature": "sha256=forged"}, inbox)[0], 401)
        self.assertEqual(self.sync.webhook(body, {"X-Notion-Signature": sig}, inbox), (200, "queued"))
        self.assertEqual(inbox.get_nowait(), "p1")
        ours = json.dumps({"authors": [{"id": BOT, "type": "bot"}], "entity": {"id": "p2", "type": "page"}}).encode()
        sig = "sha256=" + hmac.new(b"secret_x", ours, hashlib.sha256).hexdigest()
        self.assertEqual(self.sync.webhook(ours, {"X-Notion-Signature": sig}, inbox), (200, "our own change"))

    def test_a_lost_reply_is_retried_and_the_orphan_it_leaves_is_trashed(self):
        self.add(dept="eng")
        self.fake.drop_next_create = True

        def live_ids():
            return sorted(hn.plain(p["properties"].get("Holon ID")) for p in self.fake.pages.values()
                          if not p["in_trash"])

        self.sync.push()
        ids = live_ids()
        self.assertEqual(len(ids) - len(set(ids)), 1, "the create landed, its reply was lost, the retry made a twin")
        self.assertEqual(self.sync.reconcile()["orphans"], 1)
        self.assertEqual(live_ids(), sorted(set(ids)))
        self.assertEqual(self.sync.check(), [])

    def test_webhook_events_drive_every_inbound_path(self):
        inbox = queue.Queue()
        self.sync.webhook(b'{"verification_token": "secret_x"}', {}, inbox)

        def deliver(event):
            raw = json.dumps({"authors": [{"id": CHAIR, "type": "person"}], **event}).encode()
            sig = "sha256=" + hmac.new(b"secret_x", raw, hashlib.sha256).hexdigest()
            self.assertEqual(self.sync.webhook(raw, {"X-Notion-Signature": sig}, inbox), (200, "queued"))
            return self.sync.handle_event(inbox.get_nowait())

        w = self.add(dept="eng")
        approval = self.plan(w)
        self.sync.push()
        pid = self.sync._page_of("work", approval["id"])
        self.fake.human_edit(pid, {"Chair decision": {"select": {"name": "approve"}}})
        self.assertEqual(deliver({"type": "page.properties_updated", "entity": {"id": pid, "type": "page"}}),
                         "chair approve")
        gone = self.sync._page_of("work", w["id"])
        self.fake.delete_forever(gone)
        self.assertIn("recreated", deliver({"type": "page.deleted", "entity": {"id": gone, "type": "page"}}))
        self.sync.push()
        self.assertNotEqual(self.sync._page_of("work", w["id"]), gone)
        self.assertEqual(self.prop(w["id"], "Holon ID"), w["id"])
        ex = self.k.work.latest(w["id"], "execute")
        self.assertEqual(ex["status"], "ready")
        plan = self.k.work.latest(w["id"], "plan")  # pushed before the delete
        self.assertEqual(self.rel(plan["id"], "Parent"), [self.sync._page_of("work", w["id"])],
                         "pages that pointed at the deleted one point at its replacement")
        self.fake.drop_property(self.ds["work"], "Grade")
        self.assertIn("repaired", deliver({"type": "data_source.schema_updated",
                                           "entity": {"id": self.ds["work"], "type": "data_source"}}))
        self.assertEqual(self.sync.check(), [])

    def test_an_older_schema_is_upgraded_in_place(self):
        w = self.add(dept="eng", task_class="greeting")
        self.plan(w)
        self.sync.push()
        work, depts = self.fake.ds[self.ds["work"]]["properties"], self.fake.ds[self.ds["departments"]]["properties"]
        work["Outcome"] = {"id": "Outc", "name": "Outcome", "type": "rich_text", "rich_text": {}}  # a text slug
        work["Kind"] = {"id": "Kind", "name": "Kind", "type": "select", "select": {"options": []}}  # retired
        depts["Parent department"]["relation"] = {"data_source_id": self.ds["departments"],
                                                  "type": "single_property", "single_property": {}}  # one-way
        depts.pop("Sub-departments", None)
        problems = self.sync.check()
        for needle in ("'Outcome' is rich_text", "'Kind' is retired", "'Parent department' is not the dual"):
            self.assertTrue(any(needle in p for p in problems), (needle, problems))
        self.sync.check(repair=True)
        self.assertEqual(self.sync.check(), [])
        self.assertNotIn("Kind", work)
        self.sync.push()
        plan = self.k.work.latest(w["id"], "plan")
        self.assertEqual(self.prop(plan["id"], "Outcome"), "plan", "values come back as selects")

    def test_chair_created_work_carries_its_selects(self):
        self.sync.push()
        eng = self.sync._page_of("departments", self.k.org.dept("eng")["id"])
        self.fake.human_create(self.ds["work"], {
            "Title": {"title": [{"text": {"content": "Tidy logs"}}]},
            "Department": {"relation": [{"id": eng}]}, "Priority": {"select": {"name": "P1"}},
            "Risk": {"select": {"name": "low"}}, "Due": {"date": {"start": "2026-11-01"}}})
        self.sync.reconcile()
        w = self.k.wconn.execute("SELECT * FROM tasks WHERE title='Tidy logs'").fetchone()
        self.assertEqual((w["priority"], w["risk"], w["due"]), (1, "low", "2026-11-01"))

    def test_schema_drift_is_found_and_repaired(self):
        self.fake.drop_property(self.ds["work"], "Grade")
        self.assertTrue(any("Grade" in p for p in self.sync.check()))
        self.sync.check(repair=True)
        self.assertEqual(self.sync.check(), [])
