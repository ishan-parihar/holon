#!/usr/bin/env python3
"""Holon ⇄ Notion: the organization mirrored live in Notion, with the Chair's decisions flowing back.

    python3 adapters/notion/holon_notion.py setup --holon-home ~/holon --parent-page <page id> [--chair-user <id>]
    python3 adapters/notion/holon_notion.py push | reconcile | check [--repair] | verify | status
    python3 adapters/notion/holon_notion.py serve --port 8787      # webhook receiver + push/reconcile loop

Holon's SQLite is the source of truth; Notion is a projection with exactly four ways back in, all
taken only from the Chair's Notion account(s) (`chair_users`):
  - on an approve stage: set **Chair decision** to approve or reject (optional **Chair note**);
  - on work that needs input: write **Chair answer**;
  - in the Work database: a new page without a Holon ID becomes new work (Title, Intent, Acceptance,
    Department, Priority, Risk, Task class, Due);
  - nothing else: any other edit is overwritten on the next push, and a trashed page is restored.

Schema: four data sources (Departments, Employees, Work, Runs). Enumerated values are selects built
from the kernel's vocabulary (or the instance's profiles, drivers and task classes); every reference
is a dual relation, so each edge of the holarchy reads from both ends (Parent ↔ Children, Needs ↔
Needed by, Department ↔ Employees/Work, Parent department ↔ Sub-departments, Head ↔ Head of,
Reports to ↔ Direct reports, Assignee ↔ Assigned work, Approver ↔ Approvals, Run ↔ Stage/Employee).
Every column of the four tables is mirrored or listed in UNMIRRORED with where its information
lives. `check` diffs the live schema against this module and `--repair` upgrades it in place.

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
import secrets
import socket
import sqlite3
import sys
import threading
import time
from datetime import datetime
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
                e.close()
                if e.code in (429, 500, 502, 503, 504, 529) and attempt < 5:
                    time.sleep(float(e.headers.get("Retry-After") or 2 ** attempt))
                    continue
                raise NotionError(e.code, text) from None
            except OSError as e:  # reset or timed-out connection (URLError is an OSError)
                if attempt < 5:  # a POST whose reply was lost leaves an orphan page; reconcile trashes it
                    time.sleep(2 ** attempt)
                    continue
                raise NotionError(0, f"network: {e}") from None
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


def comparable(prop: dict | None) -> Any:
    """A property value (as written or as read back) reduced to what must match: text, option names,
    numbers, instants, booleans, relation targets."""
    if not prop:
        return None
    kind = next(k for k in prop if k not in ("id", "type", "has_more")) if "type" not in prop else prop["type"]
    v = prop.get(kind)
    if kind in ("title", "rich_text"):
        return "".join(x.get("plain_text") or x.get("text", {}).get("content", "") for x in v or []) or None
    if kind == "select":
        return (v or {}).get("name")
    if kind == "multi_select":
        return sorted(o["name"] for o in v or [])
    if kind == "date":
        start = (v or {}).get("start")
        # Notion keeps a date-time to the minute
        return int(datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()) // 60 if start else None
    if kind == "relation":
        return sorted(r["id"] for r in v or [])
    return v


# ── the schema: one place, generated from the kernel's vocabulary ────────────
# Rules (guarded by tests/test_notion.py): a column SQLite constrains to a set of values is a select;
# an id that points at another row is a relation, and every relation is dual, so each edge of the
# holarchy reads from both ends; nothing is mirrored that another property or relation already says.
@dataclass
class Prop:
    name: str
    kind: str                      # title | rich_text | select | multi_select | number | date | checkbox
    get: Callable[[dict], Any]
    options: tuple = ()
    column: str | None = None      # the SQLite column it mirrors, when one does (for the guard tests)
    dynamic: Callable[[Any], Any] | None = None  # options the instance defines (profiles, drivers, classes)


@dataclass
class Rel:
    name: str
    target: str                    # entity key
    get: Callable[[dict], list]    # holon ids
    synced: str                    # the reverse property Notion maintains on the target
    column: str | None = None      # the SQLite column it mirrors (None: a join table)


@dataclass
class Entity:
    key: str
    title: str
    sql: str
    props: list
    rels: list
    inputs: dict                   # Chair-writable properties: name -> schema
    retired: tuple = ()            # properties earlier versions created; `check --repair` removes them


MAX_EVENT_BYTES = 1 << 20  # Notion events carry ids, not content
SCHEMA_EVENT = "schema"  # queued for data_source/database schema webhooks
GRADES = tuple(f"G{g}" for g in range(5))
PRIORITIES = tuple(f"P{p}" for p in vocab.PRIORITIES)
WORK_STATUSES = tuple(dict.fromkeys(vocab.WORK_STATUSES + vocab.STAGE_STATUSES))
OUTCOMES = tuple(dict.fromkeys(o for outs in vocab.OUTCOMES.values() for o in outs))


def _grade(r: dict) -> str | None:
    return f"G{r['grade']}" if r.get("grade") is not None else None


def _emp(ref: str | None) -> list:
    return [ref[4:]] if ref and ref.startswith("emp:") else []


def _profiles(k) -> list:
    from holon.execution import bylaws
    return sorted(bylaws.profile_paths(k.home))


def _drivers(k) -> list:
    from holon.execution import drivers
    return sorted(drivers.available(k.home))


def _classes(k) -> list:
    configured = [c for cls in (k.config.get("classes") or {}).values() for c in cls]
    used = [r[0] for r in k.wconn.execute("SELECT DISTINCT task_class FROM tasks WHERE task_class IS NOT NULL")]
    return sorted(set(configured + used))


ENTITIES = [
    Entity("departments", "Holon · Departments", "SELECT * FROM departments ORDER BY created_at", [
        Prop("Name", "title", lambda r: r["name"], column="name"),
        Prop("Holon ID", "rich_text", lambda r: r["id"], column="id"),
        Prop("Slug", "rich_text", lambda r: r["slug"], column="slug"),
        Prop("Mission", "rich_text", lambda r: r["mission"], column="mission"),
        Prop("Profile", "select", lambda r: r["profile"], column="profile", dynamic=_profiles),
        Prop("Status", "select", lambda r: r["status"], ("active", "closed"), column="status"),
        Prop("Created", "date", lambda r: r["created_at"], column="created_at"),
    ], [
        Rel("Parent department", "departments", lambda r: [r["parent_id"]] if r["parent_id"] else [],
            synced="Sub-departments", column="parent_id"),
        Rel("Head", "employees", lambda r: [r["head_id"]] if r["head_id"] else [], synced="Head of", column="head_id"),
    ], {}),
    Entity("employees", "Holon · Employees", "SELECT * FROM employees ORDER BY hired_at", [
        Prop("Name", "title", lambda r: r["name"], column="name"),
        Prop("Holon ID", "rich_text", lambda r: r["id"], column="id"),
        Prop("Title", "rich_text", lambda r: r["title"], column="title"),
        Prop("Roles", "multi_select", lambda r: json.loads(r["roles"] or "[]"), vocab.ROLES, column="roles"),
        Prop("Task classes", "multi_select", lambda r: json.loads(r["task_classes"] or "[]"), column="task_classes",
             dynamic=_classes),
        Prop("Status", "select", lambda r: r["status"], vocab.EMPLOYEE_STATUSES, column="status"),
        Prop("Driver", "select", lambda r: r["driver"], column="driver", dynamic=_drivers),
        Prop("Job description", "rich_text", lambda r: r["job_description"], column="job_description"),
        Prop("Hired", "date", lambda r: r["hired_at"], column="hired_at"),
    ], [
        Rel("Department", "departments", lambda r: [r["dept_id"]], synced="Employees", column="dept_id"),
        Rel("Reports to", "employees", lambda r: [r["reports_to"]] if r["reports_to"] else [],
            synced="Direct reports", column="reports_to"),
    ], {}),
    Entity("work", "Holon · Work", "SELECT * FROM tasks ORDER BY depth, created_at, rowid", [
        Prop("Title", "title", lambda r: r["title"], column="title"),
        Prop("Holon ID", "rich_text", lambda r: r["id"], column="id"),
        Prop("Stage", "select", lambda r: r["stage"], vocab.STAGES, column="stage"),
        Prop("Status", "select", lambda r: r["status"], WORK_STATUSES, column="status"),
        Prop("Outcome", "select", lambda r: r["outcome"], OUTCOMES, column="outcome"),
        Prop("Grade", "select", _grade, GRADES, column="grade"),
        Prop("Grade reasons", "rich_text", lambda r: "\n".join(json.loads(r["grade_reasons"] or "[]")),
             column="grade_reasons"),
        Prop("Priority", "select", lambda r: f"P{r['priority']}", PRIORITIES, column="priority"),
        Prop("Risk", "select", lambda r: r["risk"], vocab.RISKS, column="risk"),
        Prop("Source", "select", lambda r: r["source"], vocab.SOURCES, column="source"),
        Prop("Lane", "select", lambda r: r["lane"], vocab.LANES, column="lane"),
        Prop("Playbook", "rich_text", lambda r: f"{r['playbook']} v{r['playbook_version']}" if r["playbook"] else None,
             column="playbook"),
        Prop("Task class", "select", lambda r: r["task_class"], column="task_class", dynamic=_classes),
        Prop("Chair", "checkbox", lambda r: vocab.CHAIR in (r["approver"], r["decided_by"]), column="decided_by"),
        Prop("Intent", "rich_text", lambda r: r["intent"], column="intent"),
        Prop("Acceptance", "rich_text", lambda r: r["acceptance"], column="acceptance"),
        Prop("Blocked reason", "rich_text", lambda r: r["blocked_reason"], column="blocked_reason"),
        Prop("Decision note", "rich_text", lambda r: r["decision_note"], column="decision_note"),
        Prop("Result", "rich_text", lambda r: r.get("_summary")),
        Prop("Workdir", "rich_text", lambda r: r["workdir"], column="workdir"),
        Prop("Due", "date", lambda r: r["due"], column="due"),
        Prop("Created", "date", lambda r: r["created_at"], column="created_at"),
        Prop("Updated", "date", lambda r: r["updated_at"], column="updated_at"),
    ], [
        Rel("Parent", "work", lambda r: [r["parent_id"]] if r["parent_id"] else [],
            synced="Children", column="parent_id"),
        Rel("Needs", "work", lambda r: r.get("_needs") or [], synced="Needed by"),
        Rel("Objective", "work", lambda r: [r["objective_id"]] if r["objective_id"] else [],
            synced="Serving work", column="objective_id"),
        Rel("Department", "departments", lambda r: [r["dept_id"]] if r["dept_id"] else [],
            synced="Work", column="dept_id"),
        Rel("Assignee", "employees", lambda r: [r["assignee"]] if r["assignee"] else [],
            synced="Assigned work", column="assignee"),
        Rel("Approver", "employees", lambda r: _emp(r["approver"]), synced="Approvals", column="approver"),
    ], {"Chair decision": {"select": {"options": [{"name": d} for d in vocab.OUTCOMES["approve"]]}},
        "Chair note": {"rich_text": {}}, "Chair answer": {"rich_text": {}}},
        retired=("Kind", "Attempt", "Depth")),
    Entity("runs", "Holon · Runs", "SELECT * FROM runs ORDER BY started_at", [
        Prop("Run", "title", lambda r: f"{r['kind']} · {r['id'][-8:]}"),
        Prop("Holon ID", "rich_text", lambda r: r["id"], column="id"),
        Prop("Stage kind", "select", lambda r: r["kind"], vocab.STAGES, column="kind"),
        Prop("Status", "select", lambda r: r["status"], vocab.RUN_STATUSES, column="status"),
        Prop("Driver", "select", lambda r: r["driver"], column="driver", dynamic=_drivers),
        Prop("Exit code", "number", lambda r: r["exit_code"], column="exit_code"),
        Prop("Tokens in", "number", lambda r: r["tokens_in"], column="tokens_in"),
        Prop("Tokens out", "number", lambda r: r["tokens_out"], column="tokens_out"),
        Prop("Cost", "number", lambda r: r["cost"], column="cost"),
        Prop("Started", "date", lambda r: r["started_at"], column="started_at"),
        Prop("Ended", "date", lambda r: r["ended_at"], column="ended_at"),
    ], [
        Rel("Stage", "work", lambda r: [r["task_id"]], synced="Runs", column="task_id"),
        Rel("Employee", "employees", lambda r: [r["employee_id"]] if r["employee_id"] else [],
            synced="Runs", column="employee_id"),
    ], {}),
]
BY_KEY = {e.key: e for e in ENTITIES}

# Columns deliberately not mirrored, and where their information lives instead. With ENTITIES this
# accounts for every column of the four tables; a new kernel column fails the guard test until it
# is mirrored or listed here.
UNMIRRORED = {
    "departments": {"bylaws": "the resolved bylaws; Profile names it"},
    "employees": {
        "skills": "resolved from the skill library (holon skill list)",
        "max_concurrency": "dispatch tuning (holon emp card)",
        "hired_reason": "HR history (holon emp card)", "retired_at": "HR history", "retired_reason": "HR history",
        "successor_id": "HR history; the successor inherits the role and memory",
        "key_hash": "a credential: never leaves the kernel"},
    "work": {
        "root_id": "the Parent chain", "depth": "the Parent chain", "requested_by": "Source",
        "project_ref": "the goals tree is not mirrored", "attempt": "the Runs relation",
        "max_attempts": "kernel policy", "grade_floor": "Grade reasons", "grade_proposed": "Grade reasons",
        "decided_at": "Updated", "recurring": "kernel policy (holon plan show)",
        "playbook_version": "Playbook (name and version)"},
    "runs": {
        "attempt": "the Stage's Runs", "lease_until": "kernel liveness", "heartbeat_at": "kernel liveness",
        "brief_path": "a local path (holon run show)", "log_path": "a local path (holon run show)",
        "token_hash": "a credential: never leaves the kernel",
        "tool_calls": "usage detail; metrics aggregates cost per verified result",
        "duration_s": "usage detail; metrics shows cycle time by stage",
        "harness_build": "kernel regression analysis groups judgments by it (H10)"},
}
TABLES = {"departments": "departments", "employees": "employees", "work": "tasks", "runs": "runs"}


def prop_schema(p: Prop, k=None) -> dict:
    if p.kind in ("select", "multi_select"):
        names = list(dict.fromkeys([*p.options, *(p.dynamic(k) if p.dynamic and k is not None else [])]))
        return {p.kind: {"options": [{"name": o} for o in names]}}
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
    if p.kind == "checkbox":
        return {"checkbox": bool(v)}
    raise ValueError(p.kind)


def rel_schema(rel: Rel, ds_ids: dict) -> dict:
    return {"relation": {"data_source_id": ds_ids[rel.target], "type": "dual_property",
                         "dual_property": {"synced_property_name": rel.synced}}}


def expected_schema(e: Entity, ds_ids: dict, k=None) -> dict:
    return {**{p.name: prop_schema(p, k) for p in e.props}, **e.inputs,
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
        k = self.kernel()
        try:
            for e in ENTITIES:
                props = {p.name: prop_schema(p, k) for p in e.props} | e.inputs
                db_ids[e.key], ds_ids[e.key] = self.notion.create_database(parent_page, e.title, props)
        finally:
            k.close()
        for e in ENTITIES:
            self.notion.update_data_source(ds_ids[e.key], {r.name: rel_schema(r, ds_ids) for r in e.rels})
        self.cfg.update({"parent_page": parent_page, "databases": db_ids, "data_sources": ds_ids,
                         "bot_id": self.notion.me().get("id"), "chair_users": chair_users,
                         "api_base": self.notion.base})
        self.save()
        return self.cfg

    # schema drift
    def check(self, repair: bool = False) -> list[str]:
        """Diff the live schema against ENTITIES. `repair` adds what is missing, recreates what has the
        wrong type (values come back on the next push), and removes properties a newer schema retired."""
        ds_ids, problems = self.cfg["data_sources"], []
        k = self.kernel()
        try:
            wanted = {e.key: expected_schema(e, ds_ids, k) for e in ENTITIES}
        finally:
            k.close()
        for e in ENTITIES:
            live = self.notion.data_source(ds_ids[e.key]).get("properties", {})
            add, drop = {}, {}
            for name, want in wanted[e.key].items():
                kind = next(iter(want))
                have = live.get(name)
                if not have:
                    problems.append(f"{e.key}: property {name!r} is missing")
                    add[name] = want
                elif have.get("type") != kind:
                    problems.append(f"{e.key}: property {name!r} is {have.get('type')}, expected {kind}")
                    drop[name], add[name] = None, want
                elif kind in ("select", "multi_select"):
                    names = {o["name"] for o in have.get(kind, {}).get("options", [])}
                    missing = [o["name"] for o in want[kind]["options"] if o["name"] not in names]
                    if missing:
                        problems.append(f"{e.key}: {name!r} lacks options {missing}")
                        add[name] = {kind: {"options": [{"name": n} for n in sorted(names)]
                                            + [{"name": n} for n in missing]}}
                elif kind == "relation":
                    h, w = have["relation"], want["relation"]
                    if h.get("data_source_id") != w["data_source_id"] or h.get("type") != "dual_property" or \
                            (h.get("dual_property") or {}).get("synced_property_name") != \
                            w["dual_property"]["synced_property_name"]:
                        problems.append(f"{e.key}: relation {name!r} is not the dual relation to "
                                        f"{w['dual_property']['synced_property_name']!r}")
                        drop[name], add[name] = None, want
            for name in e.retired:
                if name in live:
                    problems.append(f"{e.key}: property {name!r} is retired")
                    drop[name] = None
            if repair and (add or drop):
                if drop:  # a type cannot always be converted in place: remove, then create
                    self.notion.update_data_source(ds_ids[e.key], drop)
                if add:
                    self.notion.update_data_source(ds_ids[e.key], add)
                self.db.execute("UPDATE pages SET hash=NULL WHERE entity=?", (e.key,))  # re-push every value
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

    def forget_page(self, page_id: str) -> bool:
        """A page deleted for good: push recreates it, and re-pushes every row, because relations on
        other pages still name the old page."""
        if not self.db.execute("DELETE FROM pages WHERE page_id=?", (page_id,)).rowcount:
            return False
        self.db.execute("UPDATE pages SET hash=NULL")
        self.log("forgotten", f"page {page_id} was deleted in Notion; it is recreated on the next push")
        return True

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
        priority, due = plain(props.get("Priority")), ((props.get("Due") or {}).get("date") or {}).get("start")
        k = self.kernel()
        try:
            w = k.work.add_work(title, plain(props.get("Intent")) or title, dept=dept, source="chair",
                                acceptance=plain(props.get("Acceptance")) or None,
                                priority=int(priority[1:]) if priority in PRIORITIES else 3,
                                risk=plain(props.get("Risk")) or "normal",
                                task_class=plain(props.get("Task class")) or None, due=due)
        except (ValueError, KeyError) as err:  # e.g. a task class the department does not use
            self.log("new-work-refused", f"Notion page {page['id']}: {err}")
            return f"refused: {err}"
        finally:
            k.close()
        self.db.execute("INSERT INTO pages VALUES ('work',?,?,NULL,NULL)", (w["id"], page["id"]))
        self.log("new-work", f"{w['id']} from Notion page {page['id']}")
        return f"new work {w['id']}"

    def verify(self) -> list[str]:
        """Every live page against SQLite: one page per row, every mirrored value equal, every relation
        pointing at the right pages, and every dual relation's reverse side agreeing. Read-only."""
        k = self.kernel()
        try:
            data = self.rows(k)
        finally:
            k.close()
        live = {e.key: {p["id"]: p for p in self.notion.query(self.cfg["data_sources"][e.key])} for e in ENTITIES}
        problems = []
        for e in ENTITIES:
            pages = live[e.key]
            mapped = set()
            for r in data[e.key]:
                pid = self._page_of(e.key, r["id"])
                page = pages.get(pid)
                if page is None:
                    problems.append(f"{e.key} {r['id']}: no live page")
                    continue
                mapped.add(pid)
                props = page["properties"]
                for p in e.props:
                    want, have = comparable(prop_value(p, p.get(r))), comparable(props.get(p.name))
                    if want != have:
                        problems.append(f"{e.key} {r['id']}: {p.name} is {have!r}, SQLite says {want!r}")
                for rel in e.rels:
                    want = sorted(self._page_of(rel.target, i) or f"<unmapped {i}>" for i in rel.get(r) if i)
                    if props.get(rel.name, {}).get("has_more"):
                        continue  # ponytail: >25 targets need the property-item endpoint; not compared
                    have = comparable(props.get(rel.name))
                    if want != have:
                        problems.append(f"{e.key} {r['id']}: {rel.name} points at {have}, SQLite says {want}")
                    for target in have or []:  # the reverse side names this page
                        back = live[rel.target].get(target, {}).get("properties", {}).get(rel.synced, {})
                        if not back.get("has_more") and pid not in (comparable(back) or []):
                            problems.append(f"{e.key} {r['id']}: {rel.target} page {target} lacks it in {rel.synced}")
            for pid in set(pages) - mapped:
                problems.append(f"{e.key}: live page {pid} has no Holon row")
        return problems

    def heal(self) -> list[str]:
        """verify(), and if anything drifted, re-push every row (the next push rewrites it)."""
        with self.lock:
            problems = self.verify()
            if problems:
                self.db.execute("UPDATE pages SET hash=NULL")
                self.log("heal", f"{len(problems)} drifted: " + "; ".join(problems[:20]))
            return problems

    def poll(self) -> dict:
        """The webhook fallback, cheap enough to run every minute: ask Notion only for pages edited since
        the last poll (by last-edited time) and act on the ones a person edited."""
        with self.lock:
            since = self.cfg.get("polled_at") or self.cfg.get("reconciled_at") or "1970-01-01T00:00:00.000Z"
            started = time.strftime("%Y-%m-%dT%H:%M:00.000Z", time.gmtime(time.time() - 120))  # minute precision
            out = {"edited": 0, "handled": 0}
            for e in ENTITIES:
                edited = self.notion.query(self.cfg["data_sources"][e.key], {
                    "timestamp": "last_edited_time", "last_edited_time": {"on_or_after": since}})
                out["edited"] += len(edited)
                for p in edited:
                    if (p.get("last_edited_by") or {}).get("id") != self.cfg.get("bot_id"):
                        self.handle_page(p)
                        out["handled"] += 1
            self.cfg["polled_at"] = started
            self.save()
            return out

    def reconcile(self) -> dict:
        """The full pass: human edits since the last one, trashed, missing and orphaned pages."""
        with self.lock:
            since = self.cfg.get("reconciled_at") or "1970-01-01T00:00:00.000Z"
            started = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(time.time() - 60))  # clock skew margin
            out = {"handled": 0, "restored": 0, "orphans": 0}
            for e in ENTITIES:
                ds = self.cfg["data_sources"][e.key]
                live = self.notion.query(ds)
                seen = {p["id"] for p in live}
                known = {r[0] for r in self.db.execute("SELECT page_id FROM pages WHERE entity=?", (e.key,))}
                for p in live:
                    by_us = (p.get("last_edited_by") or {}).get("id") == self.cfg.get("bot_id")
                    if by_us and p["id"] not in known:  # a create whose reply was lost, then retried
                        self.notion.update_page(p["id"], in_trash=True)
                        self.log("orphan", f"{e.key} page {p['id']} was ours and untracked; trashed")
                        out["orphans"] += 1
                    elif p.get("last_edited_time", "") >= since and not by_us:
                        self.handle_page(p)
                        out["handled"] += 1
                for r in self.db.execute("SELECT page_id FROM pages WHERE entity=?", (e.key,)).fetchall():
                    if r[0] not in seen:  # trashed (or moved away): bring it back
                        try:
                            self.notion.update_page(r[0], in_trash=False)
                        except NotionError as err:
                            if err.status != 404:
                                raise
                            self.forget_page(r[0])
                        self.db.execute("UPDATE pages SET hash=NULL WHERE page_id=?", (r[0],))
                        out["restored"] += 1
            self.cfg["reconciled_at"] = self.cfg["polled_at"] = started
            self.save()
            return out

    # webhooks
    def verify_signature(self, raw: bytes, signature: str | None) -> bool:
        token = self.cfg.get("webhook_token")
        if not token or not signature:
            return False
        want = "sha256=" + hmac.new(token.encode(), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(want, signature)

    def _classify(self, raw: bytes, signature: str | None, path_verified: bool = True) -> tuple[int, str, list[str]]:
        """The shared receiver logic: verify, and say what the event means. Returns the receiver's
        status, a message, and the items to handle (page ids, or the schema event). A
        verification_token body rotates the stored token only when the caller sits behind the
        secret path (`path_verified`): `apply` runs wherever a host mounts it, and a public
        mount must never be able to swap the token (H-05)."""
        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError:
            return 400, "bad json", []
        if "verification_token" in body:
            if not path_verified:
                return 401, "token refused: apply is not mounted behind the secret path", []
            # Notion re-sends the token on "Resend" and when a subscription is recreated; the newest
            # one wins. Only a caller who knows the secret path gets this far.
            self.cfg["webhook_token"] = body["verification_token"]
            self.save()
            print(f"Notion sent its verification token; it is saved in {self.cfg_path} (field webhook_token). "
                  "Paste it into the connection's Webhooks tab to verify the subscription.", flush=True)
            return 200, "token stored", []
        if not self.verify_signature(raw, signature):
            return 401, "bad signature", []
        authors = body.get("authors") or []
        if authors and all(a.get("type") == "bot" for a in authors):
            return 200, "our own change", []
        entity = body.get("entity") or {}
        if entity.get("type") == "page":
            return 200, "queued", [entity["id"]]
        if entity.get("type") in ("data_source", "database") and "schema" in body.get("type", ""):
            return 200, "queued", [SCHEMA_EVENT]
        return 200, "not ours to act on", []

    def webhook(self, raw: bytes, headers: dict, inbox: "queue.Queue[str]") -> tuple[int, str]:
        code, msg, items = self._classify(raw, headers.get("X-Notion-Signature") or headers.get("x-notion-signature"))
        for i in items:
            inbox.put(i)
        return code, msg

    def apply_event(self, raw: bytes, signature: str | None, path_verified: bool = False) -> tuple[int, str]:
        """One webhook event, handled in line without the server (H8): the runtime that hosts the
        webhook route (a gateway) feeds each event here as it arrives — the same verification and
        classification, then `handle_event` runs before returning. A box that hosts the route this
        way does not need `holon-notion.service`; the poll stays the fallback. The host passes
        `--path-verified` only when its route carries the secret path (H05)."""
        code, msg, items = self._classify(raw, signature, path_verified=path_verified)
        for i in items:
            msg = self.handle_event(i)
        return code, msg

    def handle_event(self, item: str) -> str:
        """One queued webhook event: a schema change is checked and repaired; a page is handled."""
        with self.lock:
            if item == SCHEMA_EVENT:
                problems = self.check(repair=True)
                return f"schema: repaired {problems}" if problems else "schema: matches"
            try:
                page = self.notion.page(item)
            except NotionError as err:
                if err.status != 404:
                    raise
                if not self.forget_page(item):
                    return "ignored: unknown page"
                return "deleted for good; recreated on the next push"
            return self.handle_page(page)

    def webhook_path(self) -> str:
        """The secret path webhooks are served on: nobody who has not been told it can post a
        verification token first, or reach the receiver at all."""
        if not self.cfg.get("webhook_path"):
            self.cfg["webhook_path"] = "/notion/" + secrets.token_urlsafe(18)
            self.save()
        return self.cfg["webhook_path"]

    def receiver(self, port: int, inbox: "queue.Queue[str]") -> ThreadingHTTPServer:
        """The webhook endpoint on 127.0.0.1:port, answering only on the secret path."""
        sync, path = self, self.webhook_path()

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                size = int(self.headers.get("Content-Length") or 0)
                if self.path != path or size > MAX_EVENT_BYTES:
                    self.send_response(404)
                    self.end_headers()
                    return
                code, msg = sync.webhook(self.rfile.read(size), dict(self.headers), inbox)
                if code >= 400:  # e.g. Notion signing with a token we do not hold: say so, events are being lost
                    print(f"webhook refused ({code} {msg}); the poll still catches up", flush=True)
                self.send_response(code)
                self.end_headers()
                self.wfile.write(msg.encode())

            def log_message(self, format, *args):  # noqa: A002 (the base class names it so)
                pass

        server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    def upgrade_schema(self) -> list[str]:
        """Bring the live databases up to this module's schema (what a schema webhook also triggers). Run when
        the service starts, so a kernel upgrade that adds a column reaches Notion with the restart that loads
        it, instead of every push failing on a property that does not exist yet."""
        return self.check(repair=True)

    def serve(self, port: int, push_every: float = 20, poll_every: float = 60, reconcile_every: float = 900,
              verify_every: float = 3600) -> None:
        inbox: queue.Queue[str] = queue.Queue()
        print(f"schema: {self.upgrade_schema() or 'matches'}", flush=True)
        self.receiver(port, inbox)
        print(f"holon-notion: webhooks on http://127.0.0.1:{port}{self.webhook_path()} (expose it over HTTPS, "
              f"e.g. a tunnel); polling every {poll_every:g}s as the fallback", flush=True)
        last_push = last_rec = last_poll = 0.0
        last_verify = time.monotonic()
        while True:
            try:
                try:
                    item = inbox.get(timeout=1)
                    print(f"event {item}: {self.handle_event(item)}", flush=True)
                    last_push = 0  # act on it now
                except queue.Empty:
                    pass
                now = time.monotonic()
                if now - last_rec >= reconcile_every:
                    print(f"reconcile: {self.reconcile()}", flush=True)
                    last_rec = last_poll = now
                    last_push = 0
                elif now - last_poll >= poll_every:
                    r = self.poll()
                    if r["handled"]:
                        print(f"poll: {r}", flush=True)
                    last_poll = now
                    last_push = 0 if r["handled"] else last_push
                if now - last_verify >= verify_every:
                    drift = self.heal()
                    print(f"verify: {len(drift)} drifted; re-pushing" if drift else "verify: in sync", flush=True)
                    last_verify, last_push = now, 0
                if now - last_push >= push_every:
                    r = self.push()
                    if r["created"] or r["updated"]:
                        print(f"push: {r}", flush=True)
                    last_push = now
            except (NotionError, OSError) as err:  # Notion or the network is down: keep going, retry later
                self.log("error", str(err))
                print(f"error: {err}", flush=True)
                time.sleep(10)


def prefer_ipv4() -> None:
    """Try IPv4 addresses first. urllib has no happy eyeballs: it waits out every address in order,
    and on a host whose IPv6 route is dead each call would hang for minutes before reaching IPv4."""
    gai = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **kw: sorted(gai(*a, **kw), key=lambda r: r[0] != socket.AF_INET)


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
    sub.add_parser("verify", help="compare every live page, value and relation with SQLite (read-only)")
    sub.add_parser("webhook-path", help="print the secret path webhooks are served on (created once)")
    s = sub.add_parser("apply", help="one webhook event handled in line (a gateway hosting the webhook "
                     "route feeds each event here); reads stdin, or --file")
    s.add_argument("--signature", help="the event's X-Notion-Signature header value")
    s.add_argument("--file", help="the event JSON (default: stdin)")
    s.add_argument("--path-verified", action="store_true", help="the route this event arrived on carries "
                     "the secret path; without it a verification_token body is refused (H05)")
    sub.add_parser("status")
    v = sub.add_parser("serve")
    v.add_argument("--port", type=int, default=8787)
    v.add_argument("--push-every", type=float, default=20)
    v.add_argument("--poll-every", type=float, default=60, help="the webhook fallback: pages edited since")
    v.add_argument("--reconcile-every", type=float, default=900)
    v.add_argument("--verify-every", type=float, default=3600)
    a = ap.parse_args(argv)
    prefer_ipv4()
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
    elif a.cmd == "webhook-path":
        print(sync.webhook_path())
    elif a.cmd == "verify":
        problems = sync.verify()
        print("\n".join(problems) or "every page, value and relation matches SQLite")
        return 1 if problems else 0
    elif a.cmd == "status":
        n = {r[0]: r[1] for r in sync.db.execute("SELECT entity, count(*) FROM pages GROUP BY entity")}
        dirty = sync.db.execute("SELECT count(*) FROM pages WHERE hash IS NULL").fetchone()[0]
        print(json.dumps({"pages": n, "pending": dirty, "reconciled_at": sync.cfg.get("reconciled_at"),
                          "webhook_verified": bool(sync.cfg.get("webhook_token"))}, indent=2))
        for r in sync.db.execute("SELECT * FROM log ORDER BY ts DESC LIMIT 10"):
            print(f"{r['ts']} {r['kind']}: {r['detail']}")
    elif a.cmd == "apply":
        raw = (Path(a.file).read_bytes() if a.file else sys.stdin.buffer.read())
        code, msg = sync.apply_event(raw, a.signature, path_verified=a.path_verified)
        print(f"{code} {msg}")
        return 0 if code < 400 else 1
    elif a.cmd == "serve":
        sync.serve(a.port, a.push_every, a.poll_every, a.reconcile_every, a.verify_every)
    return 0


if __name__ == "__main__":
    sys.exit(main())
