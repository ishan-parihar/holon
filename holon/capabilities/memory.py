"""memory.db: institutional memory. The only writer of memory.db.

Scopes, from personal to collective:
  emp:<id>        what one employee has learned (inherited by their successor)
  dept:<slug>     what a department knows
  org             what the whole organization knows
  <project ref>   facts about one project (e.g. traj:<node>)
  class:<name>    lessons about one kind of work, across projects

Items are deduplicated on write: an exact normalized match reinforces the existing item;
a near match (token Jaccard >= 0.85) supersedes it, so memory converges instead of
accreting. Collective intelligence emerges by *promotion*: a lesson that recurs in several
employees' memories is lifted to their department, and one that recurs across several
departments is lifted to the organization.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Iterable

from holon.core import vocab
from holon.core.db import now, write_txn
from holon.core.ids import new_id

KINDS = vocab.MEMORY_KINDS
STEERING_KINDS = vocab.STEERING_KINDS
PERSONAL_KINDS = vocab.PERSONAL_KINDS
NEAR_DUP_JACCARD = 0.85
PROMOTION_JACCARD = 0.6   # lessons phrased differently by different employees still count as the same
_WORD = re.compile(r"[a-z0-9]+")


def normalize(text: str) -> str:
    return " ".join(_WORD.findall(text.lower()))


def fp(text: str) -> str:
    return hashlib.sha256(normalize(text).encode()).hexdigest()


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def jaccard(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def fts_query(text: str, max_terms: int = 12) -> str:
    """Turn free text into a safe FTS5 OR-query of distinct words (quoted, no operators)."""
    words, seen = [], set()
    for w in _WORD.findall(text.lower()):
        if len(w) < 3 or w in seen:
            continue
        seen.add(w)
        words.append(f'"{w}"')
        if len(words) >= max_terms:
            break
    return " OR ".join(words)


class Memory:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self.fts = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='items_fts'").fetchone() is not None

    # ── items ─────────────────────────────────────────────────────────────
    def add_item(self, scope: str, kind: str, content: str, provenance: str, confidence: str = "medium",
                 pinned: bool = False, review_by: str | None = None) -> dict:
        """Returns the live item plus 'action': created | reinforced | superseded."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}")
        content = content.strip()
        if not content:
            raise ValueError("content is empty")
        f = fp(content)
        with write_txn(self.conn):
            same = self.conn.execute(
                "SELECT * FROM items WHERE scope=? AND fingerprint=? AND superseded_by IS NULL", (scope, f)).fetchone()
            if same:
                self.conn.execute("UPDATE items SET uses=uses+1, pinned=max(pinned,?) WHERE id=?",
                                  (1 if pinned else 0, same["id"]))
                return {**dict(same), "action": "reinforced"}
            near = self._nearest(scope, kind, content)
            iid = new_id("item")
            self.conn.execute(
                "INSERT INTO items (id, scope, kind, content, provenance, confidence, fingerprint, pinned, valid_from,"
                " review_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (iid, scope, kind, content, provenance, confidence, f, 1 if pinned else 0, now(), review_by))
            action = "created"
            if near is not None:
                self.conn.execute("UPDATE items SET superseded_by=? WHERE id=?", (iid, near["id"]))
                self.conn.execute("INSERT OR IGNORE INTO links VALUES (?,?,?)", (f"mem:{iid}", f"mem:{near['id']}",
                                                                                "supersedes"))
                if near["pinned"]:
                    self.conn.execute("UPDATE items SET pinned=1 WHERE id=?", (iid,))
                action = "superseded"
        return {**self.get_item(iid), "action": action, "superseded": near["id"] if near is not None else None}

    def _nearest(self, scope: str, kind: str, content: str) -> sqlite3.Row | None:
        candidates = self.search(content, scopes=[scope], kinds=[kind], limit=8, raw=True)
        best, best_score = None, 0.0
        for c in candidates:
            s = jaccard(c["content"], content)
            if s > best_score:
                best, best_score = c, s
        return best if best_score >= NEAR_DUP_JACCARD else None

    def get_item(self, item_id: str) -> dict:
        r = self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
        if r is None:
            raise LookupError(f"no memory item {item_id}")
        return dict(r)

    def set_pinned(self, item_id: str, pinned: bool) -> dict:
        with write_txn(self.conn):
            self.conn.execute("UPDATE items SET pinned=? WHERE id=?", (1 if pinned else 0, item_id))
        return self.get_item(item_id)

    def retire(self, item_id: str) -> dict:
        with write_txn(self.conn):
            self.conn.execute("UPDATE items SET superseded_by='retired' WHERE id=? AND superseded_by IS NULL", (item_id,))
        return self.get_item(item_id)

    def live_items(self, scopes: Iterable[str], kinds: Iterable[str] | None = None, pinned_only: bool = False,
                   limit: int = 50) -> list[dict]:
        scopes = list(scopes)
        q = "SELECT * FROM items WHERE superseded_by IS NULL AND scope IN (%s)" % ",".join("?" * len(scopes))
        args: list[Any] = scopes
        if kinds:
            kinds = list(kinds)
            q += " AND kind IN (%s)" % ",".join("?" * len(kinds))
            args += kinds
        if pinned_only:
            q += " AND pinned=1"
        q += " ORDER BY pinned DESC, uses DESC, valid_from DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(q, [*args, limit])]

    def search(self, text: str, scopes: list[str] | None = None, kinds: list[str] | None = None,
               limit: int = 10, raw: bool = False) -> list:
        where, args = ["i.superseded_by IS NULL"], []
        if scopes:
            where.append("i.scope IN (%s)" % ",".join("?" * len(scopes)))
            args += scopes
        if kinds:
            where.append("i.kind IN (%s)" % ",".join("?" * len(kinds)))
            args += kinds
        q = fts_query(text)
        if not q:
            return []
        if self.fts:
            sql = ("SELECT i.* FROM items_fts f JOIN items i ON i.rowid=f.rowid WHERE items_fts MATCH ? AND "
                   + " AND ".join(where) + " ORDER BY bm25(items_fts) LIMIT ?")
            rows = self.conn.execute(sql, [q, *args, limit]).fetchall()
        else:
            terms = [t.strip('"') for t in q.split(" OR ")]
            like = " OR ".join("i.content LIKE ?" for _ in terms)
            sql = f"SELECT i.* FROM items i WHERE ({like}) AND " + " AND ".join(where) + " LIMIT ?"
            rows = self.conn.execute(sql, [*(f"%{t}%" for t in terms), *args, limit]).fetchall()
        return rows if raw else [dict(r) for r in rows]

    def mark_used(self, item_ids: Iterable[str]) -> None:
        ids = list(item_ids)
        if not ids:
            return
        with write_txn(self.conn):
            self.conn.execute("UPDATE items SET uses=uses+1 WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)

    # ── episodes + project state ──────────────────────────────────────────
    def add_episode(self, scope: str, digest: str, task_id: str | None = None, run_id: str | None = None,
                    outcome: str | None = None) -> str:
        eid = new_id("episode")
        with write_txn(self.conn):
            self.conn.execute("INSERT INTO episodes (id, scope, task_id, run_id, ts, outcome, digest) VALUES (?,?,?,?,?,?,?)",
                              (eid, scope, task_id, run_id, now(), outcome, digest))
        return eid

    def episodes(self, scope: str | None = None, task_id: str | None = None, limit: int = 10) -> list[dict]:
        q, args = "SELECT * FROM episodes WHERE 1=1", []
        if scope:
            q += " AND scope=?"
            args.append(scope)
        if task_id:
            q += " AND task_id=?"
            args.append(task_id)
        q += " ORDER BY ts DESC LIMIT ?"
        return [dict(r) for r in self.conn.execute(q, [*args, limit])]

    def search_episodes(self, text: str, scopes: list[str] | None = None, limit: int = 5) -> list[dict]:
        q = fts_query(text)
        if not q:
            return []
        scope_sql, args = "", []
        if scopes:
            scope_sql = " AND e.scope IN (%s)" % ",".join("?" * len(scopes))
            args = scopes
        if self.fts:
            rows = self.conn.execute(
                "SELECT e.* FROM episodes_fts f JOIN episodes e ON e.rowid=f.rowid WHERE episodes_fts MATCH ?"
                + scope_sql + " ORDER BY bm25(episodes_fts) LIMIT ?", [q, *args, limit])
        else:
            terms = [t.strip('"') for t in q.split(" OR ")]
            like = " OR ".join("e.digest LIKE ?" for _ in terms)
            rows = self.conn.execute(f"SELECT e.* FROM episodes e WHERE ({like})" + scope_sql + " LIMIT ?",
                                     [*(f"%{t}%" for t in terms), *args, limit])
        return [dict(r) for r in rows]

    def get_state(self, scope: str) -> dict | None:
        r = self.conn.execute("SELECT * FROM project_state WHERE scope=?", (scope,)).fetchone()
        return dict(r) if r else None

    def update_state(self, scope: str, run_id: str | None = None, **fields: str | None) -> dict:
        allowed = {"summary", "current_focus", "next_steps", "open_questions"}
        fields = {k: v for k, v in fields.items() if k in allowed and v is not None}
        with write_txn(self.conn):
            self.conn.execute("INSERT OR IGNORE INTO project_state (scope, updated_at) VALUES (?,?)", (scope, now()))
            for k, v in fields.items():
                self.conn.execute(f"UPDATE project_state SET {k}=? WHERE scope=?", (v, scope))
            self.conn.execute("UPDATE project_state SET updated_at=?, updated_by_run=coalesce(?, updated_by_run)"
                              " WHERE scope=?", (now(), run_id, scope))
        return self.get_state(scope)

    def log_context(self, run_id: str, chars: int, budget: int, sections: list[str], degraded: list[str]) -> None:
        with write_txn(self.conn):
            self.conn.execute("INSERT OR REPLACE INTO context_log VALUES (?,?,?,?,?,?)",
                              (run_id, now(), chars, budget, json.dumps(sections),
                               json.dumps(degraded) if degraded else None))

    # ── consolidation (the write path after every run) ────────────────────
    def consolidate(self, task: dict, run_id: str, env: dict, employee_id: str | None = None,
                    dept_scope: str | None = None, stage: str = "execute") -> dict:
        """Deterministic consolidation of any stage's result into memory. Lessons become the employee's
        own; facts, decisions and constraints belong to the project (or department); research terrain
        becomes sourced facts. Nothing here needs an LLM."""
        work_scope = task.get("project_ref") or dept_scope or "org"
        personal = f"emp:{employee_id}" if employee_id else work_scope
        prov = f"work:{run_id}"
        written, lessons = [], []
        for t in env.get("terrain") or []:
            item = self.add_item(work_scope, t.get("kind", "fact"), t["content"],
                                 f"terrain:{task['id']} {t.get('source', '')}".strip(),
                                 confidence=t.get("confidence", "medium"))
            written.append({"id": item["id"], "scope": work_scope, "action": item["action"]})
        for q in env.get("open_questions") or []:
            item = self.add_item(work_scope, "open_question", q, prov)
            written.append({"id": item["id"], "scope": work_scope, "action": item["action"]})
        for entries, default_kind in ((env.get("learnings") or [], "lesson"), (env.get("decisions") or [], "decision")):
            for e in entries:
                if isinstance(e, str):
                    e = {"content": e}
                kind = e.get("kind", default_kind)
                scope = personal if kind in PERSONAL_KINDS else work_scope
                item = self.add_item(scope, kind, e["content"], prov, confidence=e.get("confidence", "medium"))
                written.append({"id": item["id"], "scope": scope, "action": item["action"]})
                if kind == "lesson":
                    lessons.append(item)
        for b in env.get("blockers") or []:
            text = b if isinstance(b, str) else json.dumps(b)
            item = self.add_item(work_scope, "open_question", f"Blocker on '{task['title']}': {text}", prov)
            written.append({"id": item["id"], "scope": work_scope, "action": item["action"]})
        if stage != "execute":
            return {"scope": work_scope, "personal_scope": personal, "items": written, "lessons": lessons,
                    "episode": None, "state": None}
        digest = f"[{env['outcome']}] {task['title']}: {env['summary']}"
        if env.get("next_intent"):
            digest += f"\nNext: {env['next_intent']}"
        eid = self.add_episode(work_scope, digest, task["id"], run_id, env["outcome"])
        state = self.update_state(work_scope, run_id, current_focus=task["title"], next_steps=env.get("next_intent"))
        return {"scope": work_scope, "personal_scope": personal, "items": written, "lessons": lessons,
                "episode": eid, "state": state}

    def promote_recurring(self, item: dict, sibling_scopes: list[str], target_scope: str,
                          threshold: int = 2) -> dict | None:
        """Lift a lesson to a wider scope when it recurs independently in `threshold` or more
        sibling scopes (employees of a department, or departments of the organization)."""
        if item["scope"] not in sibling_scopes:
            return None
        matches = [r for r in self.search(item["content"], scopes=sibling_scopes, kinds=[item["kind"]], limit=50)
                   if jaccard(r["content"], item["content"]) >= PROMOTION_JACCARD]
        scopes = {r["scope"] for r in matches} | {item["scope"]}
        if len(scopes) < threshold:
            return None
        sources = sorted({item["id"], *(r["id"] for r in matches)})
        promoted = self.add_item(target_scope, item["kind"], item["content"],
                                 "promoted:" + ",".join(sources[:8]), confidence="high")
        if promoted["action"] == "created":
            with write_txn(self.conn):
                for sid in sources:
                    self.conn.execute("INSERT OR IGNORE INTO links VALUES (?,?,?)",
                                      (f"mem:{promoted['id']}", f"mem:{sid}", "derived_from"))
        return {**promoted, "from_scopes": sorted(scopes)}

    def record_review(self, task: dict, author: str | None, decision: str, note: str | None,
                      dept_scope: str | None = None) -> list[dict]:
        """Plan-review notes are the richest learning signal: store them where future planners
        will read them: the project (or department), the task class, and the author's own memory."""
        if not note or not note.strip():
            return []
        content = f"Plan review ({decision}) of '{task['title']}': {note.strip()}"
        out = [self.add_item(task.get("project_ref") or dept_scope or "org", "preference", content, "chair",
                             confidence="high")]
        if task.get("task_class"):
            out.append(self.add_item(f"class:{task['task_class']}", "preference", note.strip(), "chair",
                                     confidence="high"))
        if author:
            out.append(self.add_item(f"emp:{author}", "lesson", content, "chair", confidence="high"))
        return out


def scope_for(task: dict, dept_scope: str | None = None) -> str:
    return task.get("project_ref") or dept_scope or "org"
