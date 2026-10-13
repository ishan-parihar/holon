"""The Notion mirror (adapters/notion): schema, holarchy relations, Chair actions back, drift repair."""

import hashlib
import hmac
import importlib.util
import json
import queue
import sys
from pathlib import Path

from tests.fake_notion import BOT, CHAIR, FakeNotion
from tests.helpers import KernelCase

spec = importlib.util.spec_from_file_location(
    "holon_notion", Path(__file__).resolve().parents[1] / "adapters" / "notion" / "holon_notion.py")
hn = importlib.util.module_from_spec(spec)
sys.modules["holon_notion"] = hn  # dataclasses look their module up
spec.loader.exec_module(hn)


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

    def test_schema_drift_is_found_and_repaired(self):
        self.fake.drop_property(self.ds["work"], "Grade")
        self.assertTrue(any("Grade" in p for p in self.sync.check()))
        self.sync.check(repair=True)
        self.assertEqual(self.sync.check(), [])
