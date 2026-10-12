#!/usr/bin/env python3
"""Holon ⇄ Notion: the organization mirrored live in Notion, with the Chair's decisions flowing back.

    python3 adapters/notion/holon_notion.py setup --holon-home ~/holon --parent-page <page id> [--chair-user <id>]
    python3 adapters/notion/holon_notion.py push | reconcile | check [--repair] | status
    python3 adapters/notion/holon_notion.py serve --port 8787      # webhook receiver + push/reconcile loop

Holon's SQLite is the source of truth; Notion is a projection with exactly four ways back in, all
taken only from the Chair's Notion account(s) (`chair_users`):
  - on an approve stage: set **Chair decision** to approve or reject (optional **Chair note**);
  - on work that needs input: write **Chair answer**;
  - in the Work database: a new page without a Holon ID becomes new work (Title, Intent, Department);
  - nothing else: any other edit is overwritten on the next push, and a trashed page is restored.

Schema: four data sources (Departments, Employees, Work, Runs) whose properties are generated from
the kernel's vocabulary, and whose relations mirror the holarchy (Parent ↔ Children, Needs,
Department ↔ Employees, Assignee, Head, Reports to, Run → Stage/Employee). `check` diffs the live
schema against this module and `--repair` restores it.

Sync: every push hashes each row's mapped values (properties and relation targets) and writes only
rows whose hash changed: pages first, then relations, so a relation never points at a page that
does not exist yet. Webhooks (signed with the subscription's verification token) make inbound
changes near-instant; `reconcile` (every few minutes in `serve`) catches anything a webhook missed:
Notion delivers at most once and may aggregate or delay events.

State lives in $HOLON_HOME/adapters/notion/ (notion.json: ids and settings; sync.db: the page map).
The integration token comes from NOTION_API_TOKEN or the file named by `token_file` (keep it 0600).
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import queue
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))

from holon.core import vocab  # noqa: E402
from holon.core.paths import Home  # noqa: E402

API = "https://api.notion.com/v1"
VERSION = "2025-09-03"
TEXT_MAX = 2000
CHAIR_FIELDS = ("Chair decision", "Chair note", "Chair answer")


# ── the Notion API, stdlib only ──────────────────────────────────────────────
class NotionError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Notion {status}: {body[:500]}")
        self.status = status


class Notion:
    def __init__(self, token: str, base: str = API, min_interval: float = 0.34):
        self.token, self.base, self.min_interval, self._last = token, base.rstrip("/"), min_interval, 0.0

    def req(self, method: str, path: str, body: dict | None = None) -> dict:
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last)  # ~3 requests/s, Notion's average limit
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            r = urllib.request.Request(self.base + path, method=method,
                                       data=json.dumps(body).encode() if body is not None else None,
                                       headers={"Authorization": f"Bearer {self.token}", "Notion-Version": VERSION,
                                                "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(r, timeout=60) as resp:
                    return json.loads(resp.read() or b"{}")
            except urllib.error.HTTPError as e:
                text = e.read().decode(errors="replace")
                if e.code in (429, 500, 502, 503, 504, 529) and attempt < 5:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                raise NotionError(e.code, text) from None
        raise NotionError(0, "retries exhausted")

    def me(self) -> dict:
        return self.req("GET", "/users/me")

    def create_database(self, parent_page: str, title: str, properties: dict) -> tuple[str, str]:
        db = self.req("POST", "/databases", {"parent": {"type": "page_id", "page_id": parent_page},
                                             "title": [{"text": {"content": title}}],
                                             "initial_data_source": {"properties": properties}})
        return db["id"], db["data_sources"][0]["id"]

    def data_source(self, ds: str) -> dict:
        return self.req("GET", f"/data_sources/{ds}")

    def update_data_source(self, ds: str, properties: dict) -> dict:
        return self.req("PATCH", f"/data_sources/{ds}", {"properties": properties})

    def create_page(self, ds: str, properties: dict) -> dict:
        return self.req("POST", "/pages", {"parent": {"type": "data_source_id", "data_source_id": ds},
                                           "properties": properties})

    def update_page(self, page: str, properties: dict | None = None, in_trash: bool | None = None) -> dict:
        body: dict = {}
        if properties is not None:
            body["properties"] = properties
        if in_trash is not None:
            body["in_trash"] = in_trash
        return self.req("PATCH", f"/pages/{page}", body)

    def page(self, page: str) -> dict:
        return self.req("GET", f"/pages/{page}")

    def query(self, ds: str, filter: dict | None = None) -> list[dict]:
        out, cursor = [], None
        while True:
            body: dict = {"page_size": 100}
            if filter:
                body["filter"] = filter
            if cursor:
                body["start_cursor"] = cursor
            r = self.req("POST", f"/data_sources/{ds}/query", body)
            out += r.get("results", [])
            if not r.get("has_more"):
                return out
            cursor = r["next_cursor"]


# ── value builders and readers ───────────────────────────────────────────────
def _text(s: Any) -> list:
    s = "" if s is None else str(s)
    return [{"type": "text", "text": {"content": s[:TEXT_MAX]}}] if s else []


def _date(s: str | None) -> dict | None:
    return {"start": s[:23] + "Z" if len(s) > 23 else s} if s else None


def plain(prop: dict | None) -> str:
    if not prop:
        return ""
    v = prop.get(prop.get("type", ""), [])
    if isinstance(v, list):
        return "".join(x.get("plain_text") or x.get("text", {}).get("content", "") for x in v)
    if isinstance(v, dict):
        return v.get("name") or ""
    return "" if v is None else str(v)


# ── the schema: one place, generated from the kernel's vocabulary ────────────
@dataclass
class Prop:
    name: str
    kind: str                      # title | rich_text | select | multi_select | number | date
    get: Callable[[dict], Any]
    options: tuple = ()


@dataclass
class Rel:
    name: str
    target: str                    # entity key
    get: Callable[[dict], list]    # holon ids
    synced: str | None = None      # the reverse property Notion maintains (dual relation)


@dataclass
class Entity:
    key: str
    title: str
    sql: str
    props: list
    rels: list
    inputs: dict                   # Chair-writable properties: name -> schema


GRADES = tuple(f"G{g}" for g in range(5))
WORK_STATUSES = tuple(dict.fromkeys(vocab.WORK_STATUSES + vocab.STAGE_STATUSES))


def _grade(r: dict) -> str | None:
    return f"G{r['grade']}" if r.get("grade") is not None else None


ENTITIES = [
    Entity("departments", "Holon · Departments", "SELECT * FROM departments ORDER BY created_at", [
        Prop("Name", "title", lambda r: r["name"]),
        Prop("Holon ID", "rich_text", lambda r: r["id"]),
        Prop("Slug", "rich_text", lambda r: r["slug"]),
        Prop("Mission", "rich_text", lambda r: r["mission"]),
        Prop("Profile", "rich_text", lambda r: r["profile"]),
        Prop("Status", "select", lambda r: r["status"], ("active", "closed")),
        Prop("Created", "date", lambda r: r["created_at"]),
    ], [
        Rel("Parent department", "departments", lambda r: [r["parent_id"]] if r["parent_id"] else []),
        Rel("Head", "employees", lambda r: [r["head_id"]] if r["head_id"] else []),
    ], {}),
    Entity("employees", "Holon · Employees", "SELECT * FROM employees ORDER BY hired_at", [
        Prop("Name", "title", lambda r: r["name"]),
        Prop("Holon ID", "rich_text", lambda r: r["id"]),
        Prop("Title", "rich_text", lambda r: r["title"]),
        Prop("Roles", "multi_select", lambda r: json.loads(r["roles"] or "[]"), vocab.ROLES),
        Prop("Status", "select", lambda r: r["status"], vocab.EMPLOYEE_STATUSES),
        Prop("Driver", "rich_text", lambda r: r["driver"]),
        Prop("Job description", "rich_text", lambda r: r["job_description"]),
        Prop("Hired", "date", lambda r: r["hired_at"]),
    ], [
        Rel("Department", "departments", lambda r: [r["dept_id"]], synced="Employees"),
        Rel("Reports to", "employees", lambda r: [r["reports_to"]] if r["reports_to"] else []),
    ], {}),
    Entity("work", "Holon · Work", "SELECT * FROM tasks ORDER BY depth, created_at, rowid", [
        Prop("Title", "title", lambda r: r["title"]),
        Prop("Holon ID", "rich_text", lambda r: r["id"]),
        Prop("Kind", "select", lambda r: "stage" if r["stage"] else "work", ("work", "stage")),
        Prop("Stage", "select", lambda r: r["stage"], vocab.STAGES),
        Prop("Status", "select", lambda r: r["status"], WORK_STATUSES),
        Prop("Outcome", "rich_text", lambda r: r["outcome"]),
        Prop("Grade", "select", _grade, GRADES),
        Prop("Priority", "number", lambda r: r["priority"]),
        Prop("Risk", "select", lambda r: r["risk"], vocab.RISKS),
        Prop("Source", "select", lambda r: r["source"], vocab.SOURCES),
        Prop("Task class", "rich_text", lambda r: r["task_class"]),
        Prop("Approver", "rich_text", lambda r: r["approver"]),
        Prop("Intent", "rich_text", lambda r: r["intent"]),
        Prop("Acceptance", "rich_text", lambda r: r["acceptance"]),
        Prop("Blocked reason", "rich_text", lambda r: r["blocked_reason"]),
        Prop("Decision note", "rich_text", lambda r: r["decision_note"]),
        Prop("Result", "rich_text", lambda r: r.get("_summary")),
        Prop("Workdir", "rich_text", lambda r: r["workdir"]),
        Prop("Attempt", "number", lambda r: r["attempt"]),
        Prop("Depth", "number", lambda r: r["depth"]),
        Prop("Created", "date", lambda r: r["created_at"]),
        Prop("Updated", "date", lambda r: r["updated_at"]),
    ], [
        Rel("Parent", "work", lambda r: [r["parent_id"]] if r["parent_id"] else [], synced="Children"),
        Rel("Needs", "work", lambda r: r.get("_needs") or []),
        Rel("Department", "departments", lambda r: [r["dept_id"]] if r["dept_id"] else []),
        Rel("Assignee", "employees", lambda r: [r["assignee"]] if r["assignee"] else []),
    ], {"Chair decision": {"select": {"options": [{"name": d} for d in vocab.OUTCOMES["approve"]]}},
        "Chair note": {"rich_text": {}}, "Chair answer": {"rich_text": {}}}),
    Entity("runs", "Holon · Runs", "SELECT * FROM runs ORDER BY started_at", [
        Prop("Run", "title", lambda r: r["id"]),
        Prop("Holon ID", "rich_text", lambda r: r["id"]),
        Prop("Stage kind", "select", lambda r: r["kind"], vocab.STAGES),
        Prop("Status", "select", lambda r: r["status"], vocab.RUN_STATUSES),
        Prop("Driver", "rich_text", lambda r: r["driver"]),
        Prop("Exit code", "number", lambda r: r["exit_code"]),
        Prop("Tokens in", "number", lambda r: r["tokens_in"]),
        Prop("Tokens out", "number", lambda r: r["tokens_out"]),
        Prop("Cost", "number", lambda r: r["cost"]),
        Prop("Started", "date", lambda r: r["started_at"]),
        Prop("Ended", "date", lambda r: r["ended_at"]),
    ], [
        Rel("Stage", "work", lambda r: [r["task_id"]], synced="Runs"),
        Rel("Employee", "employees", lambda r: [r["employee_id"]] if r["employee_id"] else []),
    ], {}),
]
BY_KEY = {e.key: e for e in ENTITIES}


def prop_schema(p: Prop) -> dict:
    if p.kind in ("select", "multi_select"):
        return {p.kind: {"options": [{"name": o} for o in p.options]}}
    return {p.kind: {}}


def prop_value(p: Prop, v: Any) -> dict:
    if p.kind == "title":
        return {"title": _text(v)}
    if p.kind == "rich_text":
        return {"rich_text": _text(v)}
    if p.kind == "select":
        return {"select": {"name": str(v)} if v not in (None, "") else None}
    if p.kind == "multi_select":
        return {"multi_select": [{"name": str(x)} for x in v or []]}
    if p.kind == "number":
        return {"number": v}
    if p.kind == "date":
        return {"date": _date(v)}
    raise ValueError(p.kind)


def rel_schema(rel: Rel, ds_ids: dict) -> dict:
    if rel.synced:
        return {"relation": {"data_source_id": ds_ids[rel.target], "type": "dual_property",
                             "dual_property": {"synced_property_name": rel.synced}}}
    return {"relation": {"data_source_id": ds_ids[rel.target], "type": "single_property", "single_property": {}}}


def expected_schema(e: Entity, ds_ids: dict) -> dict:
    return {**{p.name: prop_schema(p) for p in e.props}, **e.inputs,
            **{r.name: rel_schema(r, ds_ids) for r in e.rels}}


# ── the sync ─────────────────────────────────────────────────────────────────
class Sync:
    def __init__(self, home: Home, notion: Notion | None = None):
        self.home = home
        self.dir = home.root / "adapters" / "notion"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.cfg_path = self.dir / "notion.json"
        self.cfg = json.loads(self.cfg_path.read_text()) if self.cfg_path.exists() else {}
        self.db = sqlite3.connect(self.dir / "sync.db", isolation_level=None, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            "CREATE TABLE IF NOT EXISTS pages (entity TEXT, holon_id TEXT, page_id TEXT NOT NULL, hash TEXT,"
            " pushed_at TEXT, PRIMARY KEY (entity, holon_id));"
            "CREATE UNIQUE INDEX IF NOT EXISTS pages_by_page ON pages(page_id);"
            "CREATE TABLE IF NOT EXISTS log (ts TEXT, kind TEXT, detail TEXT);")
        self.notion = notion or Notion(self._token(), self.cfg.get("api_base", API))
        self.lock = threading.Lock()

    def close(self) -> None:
        self.db.close()

    # config
    def _token(self) -> str:
        if os.environ.get("NOTION_API_TOKEN"):
            return os.environ["NOTION_API_TOKEN"]
        f = self.cfg.get("token_file")
        if f and Path(f).expanduser().exists():
            return Path(f).expanduser().read_text().strip()
        raise SystemExit("no Notion token: set NOTION_API_TOKEN or token_file in notion.json")

    def save(self) -> None:
        self.cfg_path.write_text(json.dumps(self.cfg, indent=2))
        self.cfg_path.chmod(0o600)  # holds the webhook verification token

    def log(self, kind: str, detail: str) -> None:
        self.db.execute("INSERT INTO log VALUES (?,?,?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), kind,
                                                           detail[:1000]))

    def kernel(self):
        from holon.execution.service import Kernel
        return Kernel(self.home)

    # setup: databases, then relations (they name each other's data sources)
    def setup(self, parent_page: str, chair_users: list[str]) -> dict:
        if self.cfg.get("data_sources"):
            raise SystemExit(f"already set up ({self.cfg_path}); use `check --repair` to fix the schema")
        ds_ids, db_ids = {}, {}
        for e in ENTITIES:
            props = {p.name: prop_schema(p) for p in e.props} | e.inputs
            db_ids[e.key], ds_ids[e.key] = self.notion.create_database(parent_page, e.title, props)
        for e in ENTITIES:
            if e.rels:
                self.notion.update_data_source(ds_ids[e.key], {r.name: rel_schema(r, ds_ids) for r in e.rels})
        self.cfg.update({"parent_page": parent_page, "databases": db_ids, "data_sources": ds_ids,
                         "bot_id": self.notion.me().get("id"), "chair_users": chair_users,
                         "api_base": self.notion.base})
        self.save()
        return self.cfg

    # schema drift
    def check(self, repair: bool = False) -> list[str]:
        ds_ids, problems = self.cfg["data_sources"], []
        for e in ENTITIES:
            live = self.notion.data_source(ds_ids[e.key]).get("properties", {})
            fix = {}
            for name, want in expected_schema(e, ds_ids).items():
                kind = next(iter(want))
                have = live.get(name)
                if not have or have.get("type") != kind:
                    problems.append(f"{e.key}: property {name!r} is {have.get('type') if have else 'missing'}, "
                                    f"expected {kind}")
                    fix[name] = want
                elif kind in ("select", "multi_select"):
                    names = {o["name"] for o in have.get(kind, {}).get("options", [])}
                    missing = [o["name"] for o in want[kind]["options"] if o["name"] not in names]
                    if missing:
                        problems.append(f"{e.key}: {name!r} lacks options {missing}")
                        fix[name] = {kind: {"options": [{"name": n} for n in sorted(names)]
                                            + [{"name": n} for n in missing]}}
                elif kind == "relation" and have["relation"].get("data_source_id") != \
                        want["relation"]["data_source_id"]:
                    problems.append(f"{e.key}: relation {name!r} points at another data source")
                    fix[name] = want
            if repair and fix:
                self.notion.update_data_source(ds_ids[e.key], fix)
        if repair and problems:
            self.log("repair", "; ".join(problems))
        return problems

    # outbound: Holon → Notion
    def rows(self, k) -> dict[str, list[dict]]:
        conn = k.wconn
        out = {e.key: [dict(r) for r in conn.execute(e.sql)] for e in ENTITIES}
        needs: dict[str, list[str]] = {}
        for r in conn.execute("SELECT task_id, needs_id FROM task_needs ORDER BY rowid"):
            needs.setdefault(r[0], []).append(r[1])
        summaries = {r[0]: r[1] for r in conn.execute("SELECT task_id, summary FROM outputs ORDER BY created_at")}
        for r in out["work"]:
            r["_needs"], r["_summary"] = needs.get(r["id"], []), summaries.get(r["id"])
        return out

    def _page_of(self, entity: str, hid: str) -> str | None:
        r = self.db.execute("SELECT page_id FROM pages WHERE entity=? AND holon_id=?", (entity, hid)).fetchone()
        return r[0] if r else None

    def push(self) -> dict:
        """Write every row whose mapped values changed: pages first, then relations."""
        with self.lock:
            k = self.kernel()
            try:
                data = self.rows(k)
            finally:
                k.close()
            ds_ids, created, updated, pending = self.cfg["data_sources"], 0, 0, []
            for e in ENTITIES:
                for r in data[e.key]:
                    props = {p.name: prop_value(p, p.get(r)) for p in e.props}  # Chair inputs: never pushed
                    rels = {rel.name: [x for x in rel.get(r) if x] for rel in e.rels}
                    h = hashlib.sha256(json.dumps([props, rels], sort_keys=True, default=str).encode()).hexdigest()
                    row = self.db.execute("SELECT page_id, hash FROM pages WHERE entity=? AND holon_id=?",
                                          (e.key, r["id"])).fetchone()
                    if row and row["hash"] == h:
                        continue
                    if row is None:
                        page = self.notion.create_page(ds_ids[e.key], props)
                        self.db.execute("INSERT INTO pages VALUES (?,?,?,NULL,NULL)", (e.key, r["id"], page["id"]))
                        created += 1
                    else:
                        self.notion.update_page(row["page_id"], props)
                        updated += 1
                    pending.append((e, r["id"], rels, h))
            for e, hid, rels, h in pending:  # every target page exists now
                if rels:
                    value = {}
                    for name, ids in rels.items():
                        target = next(x.target for x in e.rels if x.name == name)
                        value[name] = {"relation": [{"id": p} for p in (self._page_of(target, i) for i in ids) if p]}
                    self.notion.update_page(self._page_of(e.key, hid), value)
                self.db.execute("UPDATE pages SET hash=?, pushed_at=? WHERE entity=? AND holon_id=?",
                                (h, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), e.key, hid))
            return {"created": created, "updated": updated}

    # inbound: Notion → Holon
    def _entity_of_page(self, page: dict) -> Entity | None:
        ds = (page.get("parent") or {}).get("data_source_id")
        for e in ENTITIES:
            if self.cfg["data_sources"][e.key] == ds:
                return e
        return None

    def handle_page(self, page: dict) -> str:
        """One page that changed in Notion: act on the Chair's inputs, otherwise restore the truth."""
        e = self._entity_of_page(page)
        if e is None:
            return "ignored: not a Holon data source"
        editor = (page.get("last_edited_by") or {}).get("id")
        if editor == self.cfg.get("bot_id"):
            return "ignored: our own write"
        row = self.db.execute("SELECT entity, holon_id FROM pages WHERE page_id=?", (page["id"],)).fetchone()
        chair = editor in (self.cfg.get("chair_users") or [])
        if page.get("in_trash") or page.get("archived"):
            if row:
                self.notion.update_page(page["id"], in_trash=False)
                self.log("restore", f"{e.key} {row['holon_id']} was trashed in Notion; restored")
                return "restored"
            return "ignored: trashed and unknown"
        if row is None:
            if e.key == "work" and chair:
                return self._new_work(page)
            self.log("unmanaged", f"{e.key} page {page['id']} has no Holon row (edited by {editor})")
            return "unmanaged"
        result = "reverted"
        if e.key == "work" and chair:
            result = self._chair_inputs(row["holon_id"], page) or result
        # whatever happened, the next push rewrites this page from Holon's truth
        self.db.execute("UPDATE pages SET hash=NULL WHERE page_id=?", (page["id"],))
        return result

    def _chair_inputs(self, hid: str, page: dict) -> str | None:
        props = page.get("properties", {})
        decision, note, answer = (plain(props.get(n)) for n in CHAIR_FIELDS)
        if not (decision or answer):
            return None
        # cleared only here, after the kernel acted: a push never wipes a decision it has not seen
        self.notion.update_page(page["id"], {"Chair decision": {"select": None}, "Chair note": {"rich_text": []},
                                             "Chair answer": {"rich_text": []}})
        k = self.kernel()
        try:
            node = k.work.get(hid)
            if decision and node["stage"] == "approve" and node["status"] == "ready":
                if decision == "approve":
                    k.approve(hid, ("[via Notion] " + note).strip())
                else:
                    k.reject(hid, ("[via Notion] " + (note or "rejected in Notion")).strip())
                self.log("decision", f"{decision} {hid}")
                return f"chair {decision}"
            if answer and node["stage"] is None and node["status"] == "needs_input":
                k.answer(hid, "[via Notion] " + answer)
                self.log("answer", hid)
                return "chair answer"
            self.log("stale-input", f"{hid}: {decision or 'answer'} ignored (status {node['status']})")
            return "ignored: nothing to decide"
        finally:
            k.close()

    def _new_work(self, page: dict) -> str:
        props = page.get("properties", {})
        title = plain(props.get("Title"))
        if not title:
            return "ignored: untitled"
        dept_pages = [x["id"] for x in (props.get("Department") or {}).get("relation", [])]
        dept = None
        if dept_pages:
            r = self.db.execute("SELECT holon_id FROM pages WHERE entity='departments' AND page_id=?",
                                (dept_pages[0],)).fetchone()
            dept = r[0] if r else None
        k = self.kernel()
        try:
            w = k.work.add_work(title, plain(props.get("Intent")) or title, dept=dept, source="chair",
                                acceptance=plain(props.get("Acceptance")) or None)
        finally:
            k.close()
        self.db.execute("INSERT INTO pages VALUES ('work',?,?,NULL,NULL)", (w["id"], page["id"]))
        self.log("new-work", f"{w['id']} from Notion page {page['id']}")
        return f"new work {w['id']}"

    def reconcile(self) -> dict:
        """Catch what webhooks missed: human edits since the last pass, trashed and unknown pages."""
        with self.lock:
            since = self.cfg.get("reconciled_at") or "1970-01-01T00:00:00.000Z"
            started = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 60))  # clock skew margin
            out = {"handled": 0, "restored": 0}
            for e in ENTITIES:
                ds = self.cfg["data_sources"][e.key]
                live = self.notion.query(ds)
                seen = {p["id"] for p in live}
                for p in live:
                    if p.get("last_edited_time", "") >= since and (p.get("last_edited_by") or {}).get("id") != \
                            self.cfg.get("bot_id"):
                        self.handle_page(p)
                        out["handled"] += 1
                for r in self.db.execute("SELECT page_id FROM pages WHERE entity=?", (e.key,)).fetchall():
                    if r[0] not in seen:  # trashed (or moved away): bring it back
                        try:
                            self.notion.update_page(r[0], in_trash=False)
                        except NotionError as err:
                            if err.status != 404:
                                raise
                            self.db.execute("DELETE FROM pages WHERE page_id=?", (r[0],))  # recreated on push
                        self.db.execute("UPDATE pages SET hash=NULL WHERE page_id=?", (r[0],))
                        out["restored"] += 1
            self.cfg["reconciled_at"] = started
            self.save()
            return out

    # webhooks
    def verify_signature(self, raw: bytes, signature: str | None) -> bool:
        token = self.cfg.get("webhook_token")
        if not token or not signature:
            return False
        want = "sha256=" + hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, signature)

    def webhook(self, raw: bytes, headers: dict, inbox: "queue.Queue[str]") -> tuple[int, str]:
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return 400, "bad json"
        if "verification_token" in body and not self.cfg.get("webhook_token"):
            self.cfg["webhook_token"] = body["verification_token"]
            self.save()
            print(f"Notion sent its verification token; it is saved in {self.cfg_path} (field webhook_token). "
                  "Paste it into the connection's Webhooks tab to verify the subscription.", flush=True)
            return 200, "token stored"
        if not self.verify_signature(raw, headers.get("X-Notion-Signature") or headers.get("x-notion-signature")):
            return 401, "bad signature"
        authors = body.get("authors") or []
        if authors and all(a.get("type") == "bot" for a in authors):
            return 200, "our own change"
        entity = body.get("entity") or {}
        if entity.get("type") == "page":
            inbox.put(entity["id"])
        return 200, "queued"

    def serve(self, port: int, push_every: float = 20, reconcile_every: float = 300) -> None:
        inbox: queue.Queue[str] = queue.Queue()
        sync = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                code, msg = sync.webhook(raw, dict(self.headers), inbox)
                self.send_response(code)
                self.end_headers()
                self.wfile.write(msg.encode())

            def log_message(self, format, *args):  # noqa: A002 (the base class names it so)
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"holon-notion: webhooks on http://127.0.0.1:{port}/ (expose it over HTTPS, e.g. a tunnel)", flush=True)
        last_push = last_rec = 0.0
        while True:
            try:
                try:
                    pid = inbox.get(timeout=1)
                    with self.lock:
                        print(f"event {pid}: {self.handle_page(self.notion.page(pid))}", flush=True)
                    last_push = 0  # act on it now
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_rec >= reconcile_every:
                    print(f"reconcile: {self.reconcile()}", flush=True)
                    last_rec, last_push = now, 0
                if now - last_push >= push_every:
                    r = self.push()
                    if r["created"] or r["updated"]:
                        print(f"push: {r}", flush=True)
                    last_push = now
            except (NotionError, OSError) as err:  # Notion or the network is down: keep going, retry later
                self.log("error", str(err))
                print(f"error: {err}", flush=True)
                time.sleep(10)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=os.environ.get("HOLON_HOME", str(Path("~/holon").expanduser())))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="create the four databases under a page shared with the integration")
    s.add_argument("--parent-page", required=True)
    s.add_argument("--chair-user", action="append", default=[], help="Notion user id(s) whose edits are the Chair's")
    s.add_argument("--token-file", help="file holding the integration token (instead of NOTION_API_TOKEN)")
    s.add_argument("--api-base", default=API)
    sub.add_parser("push")
    sub.add_parser("reconcile")
    c = sub.add_parser("check")
    c.add_argument("--repair", action="store_true")
    sub.add_parser("status")
    v = sub.add_parser("serve")
    v.add_argument("--port", type=int, default=8787)
    v.add_argument("--push-every", type=float, default=20)
    v.add_argument("--reconcile-every", type=float, default=300)
    a = ap.parse_args(argv)
    home = Home(Path(a.holon_home).expanduser())
    if a.cmd == "setup":
        cfg_dir = home.root / "adapters" / "notion"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        cfg_path = cfg_dir / "notion.json"
        pre = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        pre |= {"api_base": a.api_base} | ({"token_file": str(Path(a.token_file).expanduser())} if a.token_file else {})
        cfg_path.write_text(json.dumps(pre, indent=2))
        cfg_path.chmod(0o600)
        sync = Sync(home)
        chair = a.chair_user
        if not chair:
            people = [u for u in sync.notion.req("GET", "/users").get("results", []) if u.get("type") == "person"]
            if len(people) != 1:
                raise SystemExit(f"{len(people)} people in the workspace: pass --chair-user <id>")
            chair = [people[0]["id"]]
        cfg = sync.setup(a.parent_page, chair)
        print(json.dumps({k: cfg[k] for k in ("databases", "data_sources", "chair_users")}, indent=2))
        print(f"push: {sync.push()}")
        return 0
    sync = Sync(home)
    if a.cmd == "push":
        print(sync.push())
    elif a.cmd == "reconcile":
        print(sync.reconcile())
    elif a.cmd == "check":
        problems = sync.check(a.repair)
        print("\n".join(problems) or "schema matches")
        return 1 if problems and not a.repair else 0
    elif a.cmd == "status":
        n = {r[0]: r[1] for r in sync.db.execute("SELECT entity, count(*) FROM pages GROUP BY entity")}
        dirty = sync.db.execute("SELECT count(*) FROM pages WHERE hash IS NULL").fetchone()[0]
        print(json.dumps({"pages": n, "pending": dirty, "reconciled_at": sync.cfg.get("reconciled_at"),
                          "webhook_verified": bool(sync.cfg.get("webhook_token"))}, indent=2))
        for r in sync.db.execute("SELECT * FROM log ORDER BY ts DESC LIMIT 10"):
            print(f"{r['ts']} {r['kind']}: {r['detail']}")
    elif a.cmd == "serve":
        sync.serve(a.port, a.push_every, a.reconcile_every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
