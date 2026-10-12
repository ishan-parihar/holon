"""Context bundles: what an agent knows when it starts a run.

Deterministic, budgeted, and fail-open: a source that is missing or errors is recorded
as degraded in memory.context_log and the bundle is still produced. Sections are
filled in priority order; each has a cap and the whole bundle has a budget.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from holon.capabilities.memory import STEERING_KINDS, Memory, fts_query, scope_for
from holon.core.db import connect
from holon.core.paths import Home
from holon.execution.work import Work

DEFAULT_BUDGET_CHARS = 24_000  # ~6k tokens


@dataclass
class Bundle:
    sections: list[tuple[str, str]] = field(default_factory=list)
    degraded: list[str] = field(default_factory=list)
    used_items: list[str] = field(default_factory=list)

    def add(self, title: str, body: str | None) -> None:
        if body and body.strip():
            self.sections.append((title, body.strip()))


def _fmt_items(items: list[dict]) -> str:
    return "\n".join(f"- [{i['kind']}{', pinned' if i['pinned'] else ''}] {i['content']} (mem:{i['id']})" for i in items)


def _lineage(home: Home, project_ref: str | None, bundle: Bundle) -> None:
    if not project_ref or not project_ref.startswith("traj:"):
        return
    if not home.trajectory_db.exists():
        bundle.degraded.append("trajectory.db absent")
        return
    node_id = project_ref.split(":", 1)[1]
    try:
        conn = connect(home.trajectory_db, readonly=True)
        chain = []
        seen = set()
        while node_id and node_id not in seen and len(chain) < 12:
            seen.add(node_id)
            r = conn.execute("SELECT id, parent_id, item_type, name, status, description FROM nodes WHERE id=?",
                             (node_id,)).fetchone()
            if r is None:
                break
            chain.append(r)
            node_id = r["parent_id"]
        conn.close()
    except sqlite3.Error as e:
        bundle.degraded.append(f"trajectory read failed: {e}")
        return
    if not chain:
        bundle.degraded.append(f"trajectory node not found: {project_ref}")
        return
    lines = []
    for depth, r in enumerate(reversed(chain)):
        desc = (r["description"] or "").strip().replace("\n", " ")
        lines.append(f"{'  ' * depth}- {r['item_type']}: {r['name']} [{r['status']}]" + (f" — {desc[:240]}" if desc else ""))
    bundle.add("Why this matters (goal lineage)", "\n".join(lines))


def _logbook(home: Home, text: str, bundle: Bundle, limit: int = 5) -> None:
    if not home.logbook_db.exists():
        return
    q = fts_query(text)
    if not q:
        return
    try:
        conn = connect(home.logbook_db, readonly=True)
        rows = conn.execute(
            "SELECT e.date, e.channel, e.title, substr(e.body,1,400) AS body FROM entries_fts f "
            "JOIN entries e ON e.rowid=f.rowid WHERE entries_fts MATCH ? ORDER BY bm25(entries_fts) LIMIT ?",
            (q, limit)).fetchall()
        conn.close()
    except sqlite3.Error as e:
        bundle.degraded.append(f"logbook read failed: {e}")
        return
    if rows:
        bundle.add("Related logbook entries (human evidence)",
                   "\n".join(f"- {r['date']} [{r['channel']}] {r['title']}: {(r['body'] or '').strip()}" for r in rows))


def _dept_slug(work: Work, dept_id: str | None) -> str | None:
    if not dept_id:
        return None
    r = work.conn.execute("SELECT slug FROM departments WHERE id=?", (dept_id,)).fetchone()
    return r["slug"] if r else None


def compile_context(home: Home, work: Work, memory: Memory, task_id: str, role: str,
                    budget: int = DEFAULT_BUDGET_CHARS, run_id: str | None = None,
                    employee: dict | None = None, predecessors: list[str] | None = None,
                    plan_id: str | None = None) -> tuple[str, dict]:
    task = work.get_task(task_id)
    task_dept = _dept_slug(work, task.get("dept_id"))
    dept_scope = f"dept:{task_dept}" if task_dept else None
    scope = scope_for(task, dept_scope)
    personal = ([f"emp:{employee['id']}"] + [f"emp:{p}" for p in predecessors or []]) if employee else []
    scopes = list(dict.fromkeys(
        personal + [scope] + ([dept_scope] if dept_scope else [])
        + ([f"dept:{employee['dept']}"] if employee else [])
        + ([f"class:{task['task_class']}"] if task.get("task_class") else []) + ["org"]))
    b = Bundle()

    # 0. who you are (persistent identity: the reason this is an employee, not a contractor)
    if employee:
        mgr = work.conn.execute("SELECT name, title FROM employees WHERE id=?", (employee.get("reports_to"),)).fetchone() \
            if employee.get("reports_to") else None
        b.add("Your role",
              f"You are **{employee['name']}**, {employee['title']} in the **{employee['dept']}** department"
              + (" (head of department)" if employee.get("is_head") else "")
              + (f", reporting to {mgr['name']} ({mgr['title']})" if mgr else ", reporting to the Chair")
              + f". Employee id `{employee['id']}`; your memory scope is `emp:{employee['id']}`."
              + (f" You inherited the role and memory of {', '.join(predecessors)}." if predecessors else "")
              + f"\n\n### Job description\n\n{employee['job_description']}")

    # 1. the task itself
    meta = [f"- id: {task['id']}", f"- status: {task['status']}", f"- priority: {task['priority']}",
            f"- risk: {task['risk']}"]
    for k in ("project_ref", "task_class", "workdir", "due"):
        if task.get(k):
            meta.append(f"- {k}: {task[k]}")
    body = f"**{task['title']}**\n\n{task['intent']}\n\n" + "\n".join(meta)
    if task.get("acceptance"):
        body += f"\n\n**Acceptance criteria:** {task['acceptance']}"
    b.add("Task", body)

    # 2. role-specific working material
    if role == "review" and plan_id:
        p = work.get_plan(plan_id)
        reasons = json.loads(p["grade_reasons"] or "[]")
        b.add(f"Plan to review (v{p['version']}, {p['id']}, grade G{p['grade']})",
              f"Author: {p['author'] or '-'}  ·  kind: {p['kind']}  ·  proposed assignee: {p['assignee'] or '-'}\n\n"
              f"Findings:\n{p['findings'] or '-'}\n\nSteps:\n{p['steps']}"
              + (f"\n\nWrites: {p['writes']}" if p["writes"] else "")
              + (f"\n\nExternal effects: {p['external_effects']}" if p["external_effects"] else "")
              + (f"\n\nRisks: {p['risks']}" if p["risks"] else "")
              + (f"\n\nRollback: {p['rollback']}" if p["rollback"] else "")
              + "\n\nWhy this grade:\n" + "\n".join(f"- {r}" for r in reasons))
    approved = work.latest_plan(task_id, "approved")
    if role in ("execute", "verify") and approved:
        b.add(f"Approved plan (v{approved['version']}, {approved['id']})",
              f"{approved['steps']}\n\nFindings at planning time:\n{approved['findings'] or '-'}"
              + (f"\n\nRisks: {approved['risks']}" if approved['risks'] else "")
              + (f"\n\nRollback: {approved['rollback']}" if approved['rollback'] else "")
              + (f"\n\nReviewer note: {approved['review_note']}" if approved['review_note'] else ""))
    if role == "verify":
        res = work.latest_result(task_id)
        if res:
            b.add("Result to verify", f"Outcome: {res['outcome']}\n\nSummary: {res['summary']}\n\n{res['documentation']}")

    # 3. previous attempts and your feedback on them (replans must see this)
    prior = [p for p in work.plans_for(task_id) if p["status"] in ("rejected", "superseded")]
    if prior:
        b.add("Previous plans and review notes", "\n\n".join(
            f"- v{p['version']} {p['status']}" + (f" — reviewer: {p['review_note']}" if p["review_note"] else "")
            + f"\n  steps: {(p['steps'] or '')[:600]}" for p in prior))
    msgs = work.messages(task_id)
    if msgs:
        b.add("Conversation on this task", "\n".join(f"- {m['ts'][:16]} {m['sender']}: {m['body']}" for m in msgs))
    prev_runs = memory.episodes(task_id=task_id, limit=5)
    if prev_runs:
        b.add("Earlier runs of this task", "\n".join(f"- {e['ts'][:16]} {e['digest']}" for e in prev_runs))

    # 4. project checkpoint + steering memory
    state = memory.get_state(scope)
    if state:
        parts = [f"{k.replace('_', ' ').title()}: {state[k]}" for k in
                 ("summary", "current_focus", "next_steps", "open_questions") if state.get(k)]
        b.add(f"Project state ({scope})", "\n".join(parts))
    steering = memory.live_items(scopes, kinds=STEERING_KINDS, limit=25)
    pinned = [i for i in memory.live_items(scopes, pinned_only=True, limit=25) if i["kind"] not in STEERING_KINDS]
    if steering or pinned:
        b.add("Constraints, decisions, preferences (follow these)", _fmt_items(pinned + steering))
        b.used_items += [i["id"] for i in pinned + steering]

    # 5. why: goal lineage + task ancestry
    _lineage(home, task.get("project_ref"), b)
    anc = work.ancestry(task_id)
    if anc:
        b.add("Parent tasks", "\n".join(f"- {a['title']} [{a['status']}]: {a['intent'][:300]}" for a in anc))
        siblings = [s for s in work.list_tasks(parent_id=task["parent_id"], limit=30) if s["id"] != task_id]
        if siblings:
            lines = []
            for s in siblings:
                r = work.latest_result(s["id"])
                lines.append(f"- {s['title']} [{s['status']}]" + (f": {r['summary'][:200]}" if r else ""))
            b.add("Sibling tasks", "\n".join(lines))

    # 6. recall: relevant memory and episodes, then logbook
    text = f"{task['title']} {task['intent']}"
    seen = set(b.used_items)
    recall = [i for i in memory.search(text, scopes=scopes, limit=10) if i["id"] not in seen]
    wider = [i for i in memory.search(text, limit=6) if i["id"] not in seen and i["scope"] not in scopes]
    if recall:
        b.add("Relevant memory", _fmt_items(recall))
        b.used_items += [i["id"] for i in recall]
    if wider:
        b.add("Possibly relevant (other projects)", "\n".join(
            f"- [{i['scope']}/{i['kind']}] {i['content']} (mem:{i['id']})" for i in wider))
    eps = memory.search_episodes(text, scopes=scopes, limit=5)
    eps = [e for e in eps if e["task_id"] != task_id]
    if eps:
        b.add("Related past work", "\n".join(f"- {e['ts'][:10]} {e['digest']}" for e in eps))
    _logbook(home, text, b)

    # render within budget, in priority order
    out, used, kept = [f"# Context for {role} run on {task_id}\n"], 0, []
    for title, body in b.sections:
        block = f"\n## {title}\n\n{body}\n"
        remaining = budget - used
        if remaining <= 200:
            b.degraded.append(f"budget exhausted before '{title}'")
            continue
        if len(block) > remaining:
            block = block[: remaining - 40] + "\n…[truncated to fit budget]\n"
            b.degraded.append(f"truncated '{title}'")
        out.append(block)
        used += len(block)
        kept.append(title)
    if b.degraded:
        out.append("\n## Context gaps\n\n" + "\n".join(f"- {d}" for d in b.degraded) + "\n")
    md = "".join(out)
    memory.mark_used(b.used_items)
    if run_id:
        memory.log_context(run_id, len(md), budget, kept, b.degraded)
    return md, {"chars": len(md), "budget": budget, "sections": kept, "degraded": b.degraded,
                "items": b.used_items}
