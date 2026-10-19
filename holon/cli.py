"""`holon`: the single entry point to the organization. Human-readable by default, `--json` for agents.

Every command is declared once in the command registry with the actor class allowed to run it:
  chair     only the Chair (Chair key)
  employee  an employee (run token or employee key) or the Chair acting --as an employee
  any       anyone, including an unauthenticated reader (read-only commands)
  kernel    the organization's own heartbeat (run with the Chair key, e.g. from a timer)
`holon help --json` prints the registry: agents, skills and `holon lint` all read it.

Exit codes: 0 ok · 1 error · 2 usage · 3 nothing to do · 4 not allowed.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from holon import __version__
from holon.core import vocab
from holon.core.paths import Home, HomeNotSet

NOTHING, FORBIDDEN = 3, 4
# what an agent may run through the broker (holon/execution/isolation.py): MCP's agent commands, plus these
BROKER_EXTRA = ("submit", "whoami", "help", "task list", "plan queue")
_FILES: dict[str, str] | None = None   # set while the broker serves a request: the files the client sent
EXPORTED_HOLON = "${HOLON:-holon}"
GRADE = {g: f"G{g} {n}" for g, n in vocab.GRADE_NAMES.items()}


# ── registry ──────────────────────────────────────────────────────────────
@dataclass
class Command:
    path: tuple[str, ...]
    fn: Callable
    actor: str
    help: str
    args: list[tuple[tuple, dict]] = field(default_factory=list)
    mutates: bool = True


REGISTRY: list[Command] = []


def arg(*a, **k) -> tuple[tuple, dict]:
    return a, k


AS = arg("--as", dest="as_", metavar="EMP_ID", help="the employee acting (implied by a token or employee key)")


def command(path: str, actor: str, help_: str, *args, mutates: bool = True):
    assert actor in vocab.ACTOR_CLASSES, actor

    def deco(fn):
        REGISTRY.append(Command(tuple(path.split()), fn, actor, help_, list(args), mutates))
        return fn
    return deco


def manifest() -> list[dict]:
    return [{"command": " ".join(c.path), "actor": c.actor, "mutates": c.mutates, "help": c.help,
             "options": [a[0] for a, _ in c.args if a and a[0].startswith("-")],
             "positionals": [a[0] for a, _ in c.args if a and not a[0].startswith("-")]} for c in REGISTRY]


# ── output helpers ────────────────────────────────────────────────────────
def _out(args, data, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        print(text if text is not None else json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _line(t: dict) -> str:
    kind = t["stage"] or "work"
    return (f"{t['id']}  {kind:<8} [{t['status']}{'/' + t['outcome'] if t.get('outcome') else ''}] P{t['priority']}  "
            f"{t['title']}" + (f"  — {t['blocked_reason']}" if t.get("blocked_reason") else ""))


def _read(path: str) -> str:
    """A file a command was given. Under the broker only what the client sent: the broker runs as the
    kernel's user and must never read a path an agent names."""
    if _FILES is not None:
        if path not in _FILES:
            raise PermissionError(f"{path} was not sent with the request (unreadable by the caller?)")
        return _FILES[path]
    return Path(path).expanduser().read_text()


def _text_arg(v: str | None) -> str | None:
    """'-' reads stdin; '@path' reads a file; anything else is literal."""
    if v == "-":
        return sys.stdin.read()
    if v and v.startswith("@"):
        return _read(v[1:])
    return v


def _me(args, k, required: bool = True) -> str | None:
    """The employee this command acts for: the authenticated employee, or the Chair's --as."""
    p = args.principal
    if p.kind == "employee":
        if args.__dict__.get("as_") and args.as_ != p.employee_id:
            raise PermissionError(f"authenticated as {p.employee_id}; --as {args.as_} is not allowed")
        return p.employee_id
    if required and not args.__dict__.get("as_"):
        raise ValueError("--as <emp_id> is required")
    return args.__dict__.get("as_")


# ── organization ──────────────────────────────────────────────────────────
@command("init", "chair", "create the instance, the executive department and the Chair key",
         arg("--rotate-chair-key", action="store_true", help="issue a new Chair key (the old one stops working)"))
def cmd_init(args, k):
    if not k.home.config_file.exists():
        k.home.config_file.write_text(
            "# holon instance config (docs/BYLAWS.md, docs/CONTRACT.md)\n[context]\nbudget_chars = 24000\n\n"
            "[planner]\nenforce_read_only = true\ntrust_harness_read_only = false\n\n"
            "[grading]\nnovelty_min_verified = 3\nsensitive_paths = []\n\n"
            "[autonomy]\n# supervised (safe default) | delegated | autonomous; keys below override the preset\n"
            "mode = \"supervised\"\n# verify_sample_rate = 0.2\n# [autonomy.dept.<slug>]\n# mode = \"delegated\"\n\n"
            "[memory]\npromotion_threshold = 2\n")
    # which checkout migrated this instance (I-04/H-09's silent half): doctor warns when the
    # running code comes from somewhere else — two checkouts, one launcher, was a live hazard
    root = Path(__file__).resolve().parents[1]
    k.auth._put("code_root", str(root))
    path = k.auth.init_chair_key(rotate=args.rotate_chair_key)
    msg = f"holon initialized at {k.home.root}"
    if path:
        msg += f"\nChair key written to {path} (mode 0600). Keep it out of every agent's reach."
    _out(args, {"home": str(k.home.root), "chair_key": str(path) if path else None}, msg)


@command("status", "any", "what needs you, and how the organization is doing", mutates=False)
def cmd_status(args, k):
    counts = k.work.counts()
    mine = k.work.awaiting_approval(vocab.CHAIR)
    delegated = [a for a in k.work.awaiting_approval(None) if a["approver"] != vocab.CHAIR]
    needs = k.work.list(status="needs_input")
    data = {**counts, "awaiting_chair": len(mine), "awaiting_delegated": len(delegated), "needs_input": len(needs),
            "audit_samples": len(k.audit_samples()), "employees": len(k.org.employees()),
            "departments": len(k.org.depts()), "auth": "configured" if k.auth.configured else "OPEN (run holon init)"}
    _out(args, data, "\n".join([
        "work: " + (", ".join(f"{s}={n}" for s, n in sorted(counts["work"].items())) or "none"),
        "ready stages: " + (", ".join(f"{s}={n}" for s, n in sorted(counts["ready_stages"].items())) or "none"),
        f"awaiting YOUR approval: {len(mine)}   awaiting CEO/HOD approval: {len(delegated)}",
        f"needs your input: {len(needs)}   audit samples to judge: {data['audit_samples']}",
        f"organization: {data['departments']} departments, {data['employees']} employees · auth: {data['auth']}"]))


@command("chair-channels", "any",
         "the Chair's channel refs ([chair] channels) — a runtime that fronts a channel runs Chair "
         "commands only for these identities (H8)", mutates=False)
def cmd_chair_channels(args, k):
    _out(args, {"channels": sorted(k.chair_channels())},
         "chair channels: " + (", ".join(sorted(k.chair_channels())) or "(none configured)"))


@command("org", "any", "the org chart", mutates=False)
def cmd_org(args, k):
    out, lines = [], []
    for d in k.org.depts():
        emps = k.org.employees(d["slug"])
        out.append({**d, "employees": emps})
        lines.append(f"{d['slug']}  ({d['name']}, profile {d.get('profile') or '-'})"
                     + (f" — {d['mission']}" if d["mission"] else ""))
        for e in sorted(emps, key=lambda e: not e["is_head"]):
            lines.append(f"  {'HOD ' if e['is_head'] else '    '}{e['id']}  {e['name']}, {e['title']}  "
                         f"[{e['status']}] roles={','.join(e['roles'])} via {e['driver']}")
    _out(args, out, "\n".join(lines) or "no departments")


@command("dept create", "chair", "create a department from a bylaws profile",
         arg("slug"), arg("--name", required=True), arg("--mission", help="text, '-' for stdin, or @file"),
         arg("--parent", help="parent department slug (default: executive)"),
         arg("--profile", default="light", help="light | standard | <instance profile> | file.toml"))
def cmd_dept_create(args, k):
    from holon.execution import bylaws
    d = k.org.create_dept(args.slug, args.name, _text_arg(args.mission), args.parent,
                          bylaws.load_profile(args.profile, k.home))
    st = k.org.staffing(d["slug"])
    _out(args, d, f"{d['id']}  {d['slug']} created with profile {d['profile']}; slots to fill: "
                  + ", ".join(f"{s['slot']} ({'+'.join(s['roles'])})" for s in st["slots"]))


@command("dept list", "any", "departments", mutates=False)
def cmd_dept_list(args, k):
    ds = k.org.depts()
    _out(args, ds, "\n".join(f"{d['slug']:<20} {d.get('profile') or '-':<10} head={d['head_name'] or '-'}  {d['name']}"
                             for d in ds))


@command("dept roles", "any", "the department's role slots and who fills them", arg("slug"), mutates=False)
def cmd_dept_roles(args, k):
    st = k.org.staffing(args.slug)
    lines = [f"{st['dept']}: profile {st['profile']} — {'complete' if st['complete'] else 'INCOMPLETE'}"]
    for s in st["slots"]:
        lines.append(f"  {'ok ' if s['ok'] else '-- '} {s['slot']:<12} {'+'.join(s['roles']):<22} "
                     + (", ".join(s["filled_by"]) or f"vacant (needs {s['min']})"))
    lines += [f"  ! {w}" for w in st["warnings"]]
    _out(args, st, "\n".join(lines))


@command("dept profile", "chair", "change a department's bylaws profile", arg("slug"), arg("profile"),
         arg("--reason", required=True))
def cmd_dept_profile(args, k):
    from holon.execution import bylaws
    d = k.org.set_profile(args.slug, bylaws.load_profile(args.profile, k.home), args.reason)
    _out(args, d, f"{d['slug']} now follows profile {d['profile']}")


@command("dept profiles", "any", "available bylaws profiles", mutates=False)
def cmd_dept_profiles(args, k):
    from holon.execution import bylaws
    out = [bylaws.load_profile(n, k.home) for n in sorted(bylaws.profile_paths(k.home))]
    _out(args, out, "\n".join(f"{g['name']:<12} v{g['version']} {g['description']}\n" + "\n".join(
        f"    {s['name']:<12} {'+'.join(s['roles'])}" for s in g["slots"]) for g in out))


@command("hr hire", "chair", "hire an employee into a slot (or roles) of a department",
         arg("--name", required=True), arg("--title", required=True), arg("--dept", required=True),
         arg("--jd", required=True, help="job description: text, '-' for stdin, or @file"),
         arg("--slot", help="fill this slot of the department's profile (gives its roles)"),
         arg("--roles", help="comma list of " + ",".join(vocab.ROLES)),
         arg("--driver", required=True, help="harness driver name (see `holon drivers`)"),
         arg("--reason", required=True), arg("--head", action="store_true", help="make this employee the head"),
         arg("--reports-to"), arg("--task-classes"), arg("--skills"), arg("--max-concurrency", type=int, default=1),
         arg("--status", default="probation", choices=["probation", "active"]))
def cmd_hr_hire(args, k):
    e = k.org.hire(name=args.name, title=args.title, dept=args.dept, job_description=_text_arg(args.jd) or "",
                   roles=args.roles.split(",") if args.roles else None, slot=args.slot, driver=args.driver,
                   reason=args.reason, head=args.head, reports_to=args.reports_to,
                   task_classes=args.task_classes.split(",") if args.task_classes else None,
                   skills=args.skills.split(",") if args.skills else None, max_concurrency=args.max_concurrency,
                   status=args.status)
    _out(args, e, f"hired {e['id']}  {e['name']}, {e['title']} in {e['dept']}" + (" (HOD)" if e["is_head"] else ""))


@command("hr reassign", "chair", "change an employee's roles: the slots it now fills, or explicit roles",
         arg("employee"), arg("--slot", action="append", help="a slot of the department's profile (repeat)"),
         arg("--roles", help="comma list of " + ",".join(vocab.ROLES)), arg("--reason", required=True))
def cmd_hr_reassign(args, k):
    e = k.org.reassign(args.employee, args.reason, roles=args.roles.split(",") if args.roles else None,
                       slots=args.slot)
    _out(args, e, f"{e['id']}  {e['name']} now {','.join(e['roles'])}")


@command("hr fire", "chair", "retire an employee; memory passes to the successor", arg("employee"),
         arg("--reason", required=True), arg("--successor", help="employee who inherits the role and memory"))
def cmd_hr_fire(args, k):
    e = k.org.fire(args.employee, args.reason, args.successor)
    k.auth.revoke_employee_key(e["id"])
    _out(args, e, f"{e['id']} retired" + (f"; memory passes to {args.successor}" if args.successor else ""))


@command("hr pause", "chair", "pause an employee", arg("employee"), arg("--reason", required=True))
def cmd_hr_pause(args, k):
    e = k.org.set_status(args.employee, "paused", args.reason)
    _out(args, e, f"{e['id']} -> {e['status']}")


@command("hr activate", "chair", "activate an employee", arg("employee"), arg("--reason", required=True))
def cmd_hr_activate(args, k):
    e = k.org.set_status(args.employee, "active", args.reason)
    _out(args, e, f"{e['id']} -> {e['status']}")


@command("hr review", "any", "an employee's record, computed from evidence", arg("employee"), mutates=False)
def cmd_hr_review(args, k):
    _out(args, k.org.review(args.employee))


@command("hr key", "chair", "issue an employee key for pull-mode sessions (replaces any previous key)",
         arg("employee"), arg("--revoke", action="store_true"))
def cmd_hr_key(args, k):
    if args.revoke:
        k.auth.revoke_employee_key(args.employee)
        _out(args, {"revoked": args.employee}, f"key revoked for {args.employee}")
        return
    key = k.auth.issue_employee_key(args.employee)
    _out(args, {"employee": args.employee, "key": key},
         f"{key}\nGive this to {args.employee}'s harness session as HOLON_KEY. It is shown once.")


@command("emp list", "any", "employees", arg("--dept"), arg("--all", action="store_true", help="include retired"),
         mutates=False)
def cmd_emp_list(args, k):
    es = k.org.employees(args.dept, include_retired=args.all)
    _out(args, es, "\n".join(f"{e['id']}  {e['dept']:<14} {e['name']}, {e['title']} [{e['status']}] "
                             f"{','.join(e['roles'])}" for e in es) or "no employees")


@command("emp show", "any", "one employee", arg("employee"), mutates=False)
def cmd_emp_show(args, k):
    e = k.org.employee(args.employee)
    _out(args, e, f"{e['name']}, {e['title']} ({e['dept']}{', HOD' if e['is_head'] else ''}) [{e['status']}]\n"
                  f"roles: {', '.join(e['roles'])} · stages: {', '.join(e['stages'])} · driver: {e['driver']}"
                  f" · reports to: {e['reports_to'] or 'Chair'}\n\n{e['job_description']}")


@command("emp card", "any", "the identity card a harness injects into an employee's system prompt",
         arg("employee"), mutates=False)
def cmd_emp_card(args, k):
    card = k.identity(args.employee)
    _out(args, {"employee": args.employee, "markdown": card}, card)


# ── work ──────────────────────────────────────────────────────────────────
@command("task add", "chair", "add work (a root, or nested under --parent)",
         arg("title"), arg("--intent", required=True, help="text, '-' for stdin, or @file"),
         arg("--acceptance", help="how done will be judged"), arg("--project", help="project ref, e.g. traj:<node>"),
         arg("--parent"), arg("--after", action="append", default=[], help="work this waits for (repeat)"),
         arg("--dept"), arg("--assign", help="employee id"), arg("--priority", type=int, default=3,
                                                                  choices=list(vocab.PRIORITIES)),
         arg("--task-class"), arg("--risk", default="normal", choices=list(vocab.RISKS)), arg("--workdir"),
         arg("--due"), arg("--max-attempts", type=int, default=2),
         arg("--lane", default="auto", choices=["auto", "fast", "full"],
             help="auto: the decision model picks; fast runs only if the kernel grades the work G0"))
def cmd_task_add(args, k):
    intent = _text_arg(args.intent)
    routed = k.route_dept(args.title, intent) if args.dept == "auto" else None
    lane, cls = k.intake_lane(args.title, intent, None if args.lane == "auto" else args.lane)
    t = k.work.add_work(args.title, intent, acceptance=_text_arg(args.acceptance),
                        project_ref=args.project, parent_id=args.parent, needs=args.after,
                        dept=routed["dept"] if routed else args.dept,
                        assignee=args.assign, priority=args.priority, source=vocab.CHAIR, task_class=args.task_class,
                        risk=args.risk, workdir=str(Path(args.workdir).expanduser().resolve()) if args.workdir else None,
                        due=args.due, max_attempts=args.max_attempts, lane=lane)
    stage = k.work.stages(t["id"])
    _out(args, {**t, "routing": routed, "classification": cls},
         _line(t) + f"\n  lane: {t['lane']}" + (f" (asked for {lane})" if t["lane"] != lane else "")
         + (f"\n  department: {routed['dept'] or 'executive (unsure)'}" if routed else "")
         + (f"\n  first stage: {stage[0]['stage']} {stage[0]['id']}" if stage else ""))


@command("task list", "any", "work nodes (or stages with --stages)", arg("--status"), arg("--project"),
         arg("--dept"), arg("--assignee"), arg("--stage", choices=list(vocab.STAGES)),
         arg("--stages", action="store_true", help="list stage nodes instead of work"),
         arg("--limit", type=int, default=50), mutates=False)
def cmd_task_list(args, k):
    dept_id = k.org.dept(args.dept)["id"] if args.dept else None
    ts = k.work.list(kind="stage" if (args.stages or args.stage) else "work", status=args.status, stage=args.stage,
                     project_ref=args.project, dept_id=dept_id, assignee=args.assignee, limit=args.limit)
    _out(args, ts, "\n".join(_line(t) for t in ts) or "nothing")


@command("task show", "any", "one work node: its pipeline, outputs, children and conversation", arg("task_id"),
         mutates=False)
def cmd_task_show(args, k):
    w = k.work
    t = w.work_of(args.task_id)
    stages = w.stages(t["id"])
    data = {"work": t, "stages": [{**s, "output": w.output(s["id"])} for s in stages],
            "children": w.children(t["id"]), "needs": w.needs(t["id"]), "messages": w.messages(t["id"]),
            "events": w.events(t["id"], limit=20)}
    lines = [_line(t), "", t["intent"]]
    if t.get("acceptance"):
        lines += ["", f"acceptance: {t['acceptance']}"]
    lines.append("\npipeline:")
    for s in data["stages"]:
        o = s["output"] or {}
        lines.append("  " + _line(s) + (f"  G{s['grade']}" if s["grade"] is not None else "")
                     + (f" → {s['approver']}" if s["approver"] else "")
                     + (f"\n      {(o.get('summary') or '')[:200]}" if o.get("summary") else "")
                     + (f"\n      note ({s['decided_by']}): {s['decision_note']}" if s["decision_note"] else ""))
    ex = w.last_execute_output(t["id"])
    if ex and ex.get("documentation"):
        lines += ["", f"result (verified={ex['verified']}):", ex["documentation"]]
    for c in data["children"]:
        lines.append("  child " + _line(c))
    _out(args, data, "\n".join(lines))


@command("task tree", "any", "the holarchy under a work node", arg("task_id"), mutates=False)
def cmd_task_tree(args, k):
    w = k.work

    def walk(node, depth):
        rows = [("  " * depth) + _line(node)]
        for c in w.children(node["id"]):
            rows += walk(c, depth + 1)
        return rows
    root = w.work_of(args.task_id)
    _out(args, {"root": root["id"]}, "\n".join(walk(root, 0)))


@command("task cancel", "chair", "cancel work and its open stages and children", arg("task_id"),
         arg("--note", required=True))
def cmd_task_cancel(args, k):
    _out(args, k.work.cancel(args.task_id, args.note), f"cancelled {args.task_id}")


@command("task retry", "chair", "give failed work another execution attempt", arg("task_id"))
def cmd_task_retry(args, k):
    _out(args, k.work.retry(args.task_id), f"{args.task_id}: new execute stage queued")


@command("task replan", "chair", "queue a fresh plan stage", arg("task_id"), arg("--note"))
def cmd_task_replan(args, k):
    _out(args, k.work.replan(args.task_id, args.note or "chair replan"), f"{args.task_id}: new plan stage queued")


@command("task answer", "chair", "answer a planner's question", arg("task_id"), arg("text"))
def cmd_task_answer(args, k):
    _out(args, k.answer(args.task_id, args.text), f"{args.task_id}: answered; a new plan stage is queued")


def _plan_text(k, approval: dict) -> str:
    plan = k.work.plan_for_approval(approval["id"])
    body = plan["output"]["body"]
    steps = body.get("steps") or body.get("children") or []
    if isinstance(steps, list):
        steps = "\n".join(f"  {s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)}" for s in steps)
    parts = [f"{approval['id']}  [{approval['status']}] {body['kind']}  {GRADE[approval['grade']]} → "
             f"{approval['approver']}  work={approval['parent_id']}  plan={plan['id']}  author={body.get('author') or '-'}",
             "why this grade: " + "; ".join(json.loads(plan["grade_reasons"] or "[]")),
             f"\nfindings:\n  {body.get('findings') or body.get('rationale')}", f"\nsteps:\n{steps}"]
    for key in ("writes", "external_effects", "assignee", "risks", "rollback", "estimate"):
        if body.get(key):
            parts.append(f"\n{key}: {body[key]}")
    return "\n".join(parts)


@command("plan queue", "any", "plans awaiting approval", arg("--approver", default=vocab.CHAIR,
                                                              help="chair | emp:<id> | all"), mutates=False)
def cmd_plan_queue(args, k):
    q = k.work.awaiting_approval(None if args.approver == "all" else args.approver)
    _out(args, q, "\n".join(f"{a['id']}  G{a['grade']} P{a['priority']} → {a['approver']:<10} {a['work_title']}"
                            for a in q) or "nothing awaiting approval")


@command("plan show", "any", "a plan awaiting (or past) approval: by approval, plan or work id", arg("ref"),
         mutates=False)
def cmd_plan_show(args, k):
    a = k.work.get(args.ref)
    if a["stage"] != "approve":
        a = k.work.resolve_approval(args.ref) if a["stage"] != "plan" else \
            k.work.get(k.work.conn.execute("SELECT task_id FROM task_needs WHERE needs_id=?", (a["id"],)).fetchone()[0])
    _out(args, {"approval": a, "plan": k.work.plan_for_approval(a["id"])}, _plan_text(k, a))


@command("plan approve", "chair", "approve a plan (approval, plan or work id)", arg("ref"), arg("--note"),
         arg("--recurring", action="store_true", help="also approve it as a template for a recurring schedule"))
def cmd_plan_approve(args, k):
    out = k.approve(args.ref, args.note, recurring=args.recurring)
    extra = f" ({len(out['children'])} sub-tasks created)" if out["children"] else ""
    _out(args, out, f"approved {out['approval']['id']}{extra}; work {out['work']['id']} -> {out['work']['status']}")


@command("plan reject", "chair", "reject a plan with what must change", arg("ref"), arg("--note", required=True))
def cmd_plan_reject(args, k):
    out = k.reject(args.ref, args.note)
    _out(args, out, f"rejected {out['approval']['id']}; work {out['work']['id']} -> {out['work']['status']}")


@command("plan grade", "chair", "regrade a pending plan (reroutes it)", arg("ref"), arg("grade"),
         arg("--note", required=True))
def cmd_plan_grade(args, k):
    a = k.regrade(args.ref, int(args.grade.upper().lstrip("G")), args.note)
    _out(args, a, f"{a['id']} regraded to {GRADE[a['grade']]} → {a['approver']}")


@command("claim", "employee", "claim the next ready stage you may do (exit 3 if none)",
         AS, arg("--stage", choices=list(vocab.STAGES), help="only this stage (default: any of your roles' stages)"),
         arg("--driver"), arg("--task", help="only this work or stage"))
def cmd_claim(args, k):
    me = _me(args, k, required=args.principal.kind != "chair")
    c = k.claim(args.stage, me, args.driver, args.task)
    if c is None:
        _out(args, {"claimed": None}, "nothing to claim")
        return NOTHING
    data = {"run_id": c.run["id"], "task_id": c.node["id"], "stage": c.node["stage"], "work_id": c.node["parent_id"],
            "brief_dir": str(c.brief_dir), "brief_md": str(c.brief_dir / "BRIEF.md"),
            "result_path": str(c.brief_dir / "result.json"), "lease_until": c.run["lease_until"], "token": c.token}
    _out(args, data, f"claimed {c.node['stage']} {c.node['id']} (work {c.node['parent_id']}): run {c.run['id']}\n"
                     f"read  {data['brief_md']}\nwrite {data['result_path']}\n"
                     f"then  HOLON_RUN_TOKEN={c.token} holon submit {c.run['id']}")


def _own_run(args, k, run_id: str) -> dict:
    run = k.work.get_run(run_id)
    p = args.principal
    if p.kind == "employee" and (run["employee_id"] != p.employee_id or (p.run_id and p.run_id != run_id)):
        raise PermissionError(f"run {run_id} is not yours")
    return run


@command("submit", "employee", "hand in a run's result (result.json, or --file)", arg("run_id"), arg("--file"))
def cmd_submit(args, k):
    _own_run(args, k, args.run_id)
    env = json.loads(_read(args.file)) if args.file else None
    out = k.submit(args.run_id, env, defer=args.principal.kind == "employee")
    w = out.get("work") or {}
    msg = f"run {args.run_id} ({out['stage']}) ingested; work {w.get('id')} -> {w.get('status')}"
    if out.get("approval"):
        msg += f"\nplan graded {GRADE[out['approval']['grade']]} → {out['approval']['approver']}"
    if out.get("breach"):
        msg += "\ncontract breach: " + "; ".join(out["breach"])
    for p in (out.get("memory") or {}).get("promotions", []):
        msg += f"\nlesson promoted to {p['to']}: {p['item']}"
    _out(args, out, msg)


@command("whoami", "any", "who you are authenticated as, and the run you are in", mutates=False)
def cmd_whoami(args, k):
    p = args.principal
    data = {"principal": p.kind, "employee_id": p.employee_id, "run_id": p.run_id, "home": str(k.home.root)}
    if p.run_id:
        r = k.work.get_run(p.run_id)
        s = k.work.get(r["task_id"])
        data["stage"] = {key: s[key] for key in ("id", "stage", "parent_id", "title")}
        data["brief_md"] = str(k.home.run_dir(p.run_id) / "BRIEF.md")
    _out(args, data, f"{p.kind}" + (f" {p.employee_id}" if p.employee_id else "") + (f", run {p.run_id}" if p.run_id else ""))


@command("run show", "any", "a run and its stage", arg("run_id"), mutates=False)
def cmd_run_show(args, k):
    r = k.work.get_run(args.run_id)
    r.pop("token_hash", None)
    _out(args, {"run": r, "stage": k.work.get(r["task_id"]), "output": k.work.output(r["task_id"])})


@command("run heartbeat", "employee", "extend a run's lease on long work", arg("run_id"))
def cmd_run_heartbeat(args, k):
    _own_run(args, k, args.run_id)
    r = k.work.heartbeat(args.run_id)
    _out(args, {"run": r["id"], "lease_until": r["lease_until"]}, f"lease extended to {r['lease_until']}")


@command("run finish", "kernel", "a supervisor hands a finished body in: salvage result.json, submit it, "
         "run the checks and the judge (CONTRACT §12)", arg("run_id"),
         arg("--exit", type=int, default=0, help="the body's exit code"))
def cmd_run_finish(args, k):
    out = k.finish_run(args.run_id, args.exit)
    _out(args, out, f"run {args.run_id} finished; work -> {(out.get('work') or {}).get('status')}")


@command("verify", "chair", "verify a result yourself", arg("task_id"), arg("verdict", choices=["pass", "fail"]),
         arg("--note", required=True))
def cmd_verify(args, k):
    out = k.verify(args.task_id, args.verdict, args.note)
    _out(args, out, f"{out['work']['id']} -> {out['work']['status']}")


@command("dispatch", "kernel", "claim a stage for an employee and run its harness driver", AS,
         arg("--stage", choices=list(vocab.STAGES)), arg("--task"), arg("--schedule", help=argparse.SUPPRESS),
         arg("--prepare", action="store_true", help="claim and write the brief, policy.json and the run "
             "token, and print them as JSON without running the body (an external supervisor runs it; "
             "CONTRACT §12)"),
         arg("--runs-as-agent-user", action="store_true", help="the supervisor's promise: it runs the body as "
             "the isolated agent user through the runner. Required with --prepare under isolation"))
def cmd_dispatch(args, k):
    if not args.as_:
        raise ValueError("--as <emp_id> is required")
    if args.prepare:
        if k.isolation and not args.runs_as_agent_user:
            raise PermissionError("--prepare under isolation needs --runs-as-agent-user: a supervisor that "
                                  "runs the body as itself defeats the trust boundary (CONTRACT §12)")
        out = k.prepare_dispatch(args.as_, args.stage, args.task)
        if out is None:
            _out(args, {"prepared": None}, f"nothing to do for {args.as_}")
            return NOTHING
        _out(args, {"prepared": out}, f"{out['stage']} prepared: run {out['run']} (finish with `holon run finish`)")
        return
    try:
        out = k.dispatch(args.as_, args.stage, args.task)
    except Exception:
        if args.schedule:
            k.schedules.record_outcome(args.schedule, False)
        raise
    if args.schedule:
        k.schedules.record_outcome(args.schedule, True)
    if out is None:
        _out(args, {"dispatched": None}, f"nothing to do for {args.as_}")
        return NOTHING
    w = out.get("work") or {}
    _out(args, out, f"{out.get('stage')} run done; work {w.get('id')} -> {w.get('status')}")


@command("tick", "kernel", "self-healing sweep, feedback-loop triggers, due schedules (call from any timer)",
         arg("--foreground", action="store_true"))
def cmd_tick(args, k):
    out = k.tick(foreground=args.foreground)
    _out(args, out, f"swept {len(out['swept'])} expired leases; {len(out['triggered'])} triggers; "
                    f"fired {len(out['fired'])} schedules")


@command("schedule add", "chair", "when an employee wakes (wake) or a recurring plan fires (recurring)",
         AS, arg("--kind", required=True, choices=list(vocab.SCHEDULE_KINDS)), arg("--cron", required=True,
                                                                                 help="5-field cron, UTC"),
         arg("--stage", choices=list(vocab.STAGES), help="wake: only this stage"),
         arg("--plan", help="recurring: the plan approved with --recurring"))
def cmd_schedule_add(args, k):
    row = k.schedules.add(args.kind, args.cron, employee_id=args.as_, stage=args.stage, template_id=args.plan)
    _out(args, row, f"{row['id']}  {row['kind']} '{row['cron']}' next {row['next_run_at']}")


@command("routine add", "chair", "a recurring job, in the cheapest lane that does it reliably (script, fast, full); "
         "the classifier enforces the lane and the routine goes to its approver", arg("title"),
         arg("--intent", required=True, help="what each firing does: text, '-' for stdin, or @file"),
         arg("--dept", required=True),
         arg("--cron", help="5-field cron, e.g. '0 7 * * *'; or --on for an event trigger"),
         arg("--on", metavar="event:<pattern>", help="fire on events instead of a cron: the runtime that sees "
             "a matching event fires it (`holon routine fire`), once per cooldown; Holon never matches events"),
         arg("--lane", choices=list(vocab.LANES), help="default: the classifier's"),
         arg("--command", help="the script lane: a command the kernel runs (no agent)"),
         arg("--rubric", action="append", default=[], help="a criterion each result is judged on (repeat)"),
         arg("--why", help="required for a costlier lane than the classifier's, or an agent for a deterministic job"),
         arg("--deliver", action="store_true", help="send each result to the Chair through [notify] (a digest)"),
         arg("--workdir"), arg("--task-class"), arg("--risk", default="normal", choices=list(vocab.RISKS)),
         arg("--project"))
def cmd_routine_add(args, k):
    a = k.add_routine(args.title, _text_arg(args.intent), dept=args.dept, cron=args.cron, event=args.on,
                      lane=args.lane,
                      command=args.command, rubric=args.rubric, why=args.why, task_class=args.task_class,
                      workdir=str(Path(args.workdir).expanduser().resolve()) if args.workdir else None,
                      risk=args.risk, project_ref=args.project, deliver=args.deliver)
    plan = k.work.plan_for_approval(a["id"])
    _out(args, {"approval": a, "plan": plan}, _plan_text(k, a) + "\n\nOn approval the schedule starts.")


@command("routine fire", "kernel", "fire an event routine: the runtime that saw a matching event calls this "
         "(once per cooldown, with the payload on the work)", arg("schedule_id"),
         arg("--payload", help="what the event carried, taken literally: never a file reference — a runtime "
             "relaying channel text MUST NOT let that text name a path. Use --payload-file for a file the "
             "runtime itself chose"),
         arg("--payload-file", help="read the payload from this file (the runtime's own choice of path)"))
def cmd_routine_fire(args, k):
    payload = args.payload
    if args.payload_file:
        payload = _read(args.payload_file)
    out = k.fire_routine(args.schedule_id, payload)
    sk = out.get("skipped")
    _out(args, out, f"skipped: {sk}" if sk else f"work {out.get('recurring_work')} fired")
    if sk:   # H-13: a skip is not a firing; runtimes count them (CONTRACT §13)
        return NOTHING


def legacy_cron(path: Path) -> dict:
    """A legacy cron-job protocol (`cron-jobs/<job>.md`: YAML-ish frontmatter and a markdown body)
    as routine fields. The frontmatter subset those files use: `key: value`, quoted values, indented
    continuation lines, and `- item` lists."""
    text = path.read_text()
    meta: dict = {}
    body = text
    if text.startswith("---\n") and "\n---" in text[4:]:
        end = text.index("\n---", 4)
        key = None
        for line in text[4:end].splitlines():
            if line.startswith(("  ", "\t")) and key and not line.strip().startswith("- "):
                meta[key] = f"{meta[key]} {line.strip()}".strip()
            elif line.strip().startswith("- ") and key:
                meta[key] = (meta[key] if isinstance(meta[key], list) else []) + [line.strip()[2:].strip().strip("\"'")]
            elif ":" in line and not line.startswith(" "):
                key, v = line.split(":", 1)
                key, meta[key.strip()] = key.strip(), v.strip().strip("\"'")
        body = text[end + 4:].lstrip("\n")
    pool = path.resolve().parent.parent
    script = None
    if str(meta.get("no_agent")).lower() == "true" and meta.get("script"):
        script = pool / "scripts" / meta["script"]  # the pool's copy is canonical; lint flags it if missing
    return {"title": meta.get("name") or path.stem, "cron": meta.get("schedule", ""),
            "intent": ((meta.get("description") or "") + "\n\n" + body.strip())[:20000].strip(),
            "command": (f"python3 {script}" if str(script).endswith(".py") else str(script)) if script else None,
            "deliver": bool(meta.get("deliver")), "workdir": str(pool), "paused": meta.get("status") != "active",
            "source": str(path)}


@command("routine import", "chair", "propose routines from legacy cron-job protocols (cron-jobs/*.md); a dry "
         "run unless --apply", arg("files", nargs="+"), arg("--dept", required=True),
         arg("--apply", action="store_true", help="create the routine proposals (each goes to its approver)"))
def cmd_routine_import(args, k):
    rows = []
    for f in args.files:
        r = legacy_cron(Path(f).expanduser())
        if args.apply and not r["paused"]:
            try:
                a = k.add_routine(r["title"], r["intent"], dept=args.dept, cron=r["cron"], command=r["command"],
                                  workdir=r["workdir"], deliver=r["deliver"], strict=False, imported=True)
                r.update(approval=a["id"], approver=a["approver"], grade=a["grade"])
            except (ValueError, LookupError) as e:
                r["error"] = str(e)
        rows.append({k_: v for k_, v in r.items() if k_ != "intent"})
    _out(args, rows, "\n".join(f"{'paused ' if r['paused'] else ''}{r['title']:<36} `{r['cron']}` "
                               f"{'script: ' + r['command'] if r['command'] else 'agent'}{' deliver' if r['deliver'] else ''}"
                               + (f"  -> {r['approval']} ({r['approver']}, G{r['grade']})"
                                  + (", starts OFF: outside-reach import, enable per job" if r.get("grade", 0) >= 3
                                     else ", starts on approval") if r.get("approval") else "")
                               + (f"  !! {r['error']}" if r.get("error") else "") for r in rows))


@command("routine list", "any", "routines: lane, schedule, firings and outcomes", mutates=False)
def cmd_routine_list(args, k):
    rs = k.routines()
    _out(args, rs, "\n".join(f"{r['schedule']}  {'on ' if r['enabled'] else 'off'} {r['lane']:<6} {r['cron']:<14} "
                             f"{r['title']}  ({r['fired']} fired, {r['done']} done, {r['failed']} failed)"
                             for r in rs) or "no routines")


@command("smoke", "chair", "run the smoke checks: the infrastructure each department runs on ([smoke]); a "
         "critical failure blocks that department's agents", arg("--dept"), arg("--cached", action="store_true",
                                                                               help="the last results, if fresh"))
def cmd_smoke(args, k):
    rs = k.smoke_results() if args.cached else k.run_smoke(args.dept)
    bad = [r for r in rs if not r["ok"]]
    _out(args, rs, "\n".join(f"{'ok  ' if r['ok'] else ('FAIL' if r['critical'] else 'warn')} {r['dept'] or 'org':<12} "
                             f"{r['name']} ({r['seconds']}s)" + ("" if r["ok"] else f"\n     {r['tail'][-300:]}")
                             for r in rs) or "no smoke checks ([smoke] in holon.toml)")
    return 1 if any(r["critical"] for r in bad) else 0


@command("objective add", "chair", "an objective: decomposed into work and routines, reviewed against its key results "
         "until they are met", arg("title"), arg("--intent", required=True, help="text, '-' for stdin, or @file"),
         arg("--dept", required=True), arg("--key-result", action="append", default=[], dest="key_results",
                                           help="a measurable result that says the objective is met (repeat)"),
         arg("--project"), arg("--workdir"))
def cmd_objective_add(args, k):
    o = k.add_objective(args.title, _text_arg(args.intent), dept=args.dept, key_results=args.key_results,
                        project_ref=args.project,
                        workdir=str(Path(args.workdir).expanduser().resolve()) if args.workdir else None)
    _out(args, o, _line(o) + f"\n  first stage: {k.work.stages(o['id'])[0]['stage']}")


@command("objective show", "any", "an objective's dashboard: key results, its work, its routines and their playbooks",
         arg("objective_id"), mutates=False)
def cmd_objective_show(args, k):
    text = k.objective_dashboard(args.objective_id)
    _out(args, {"objective": args.objective_id, "dashboard": text}, text)


@command("playbook list", "any", "playbooks: each routine's and task class's protocol and rubric, with its version",
         arg("--dept"), mutates=False)
def cmd_playbook_list(args, k):
    from holon.capabilities import playbooks
    rows = []
    for d in k.org.depts():
        if args.dept and d["slug"] != args.dept:
            continue
        for name in k.skills.names(d["slug"]):
            pb = playbooks.read(k.skills, d["slug"], name)
            if pb and pb["layer"].startswith("dept:"):
                ev = k.playbook_evidence(name, d["id"], pb["version"])
                rows.append({"dept": d["slug"], "name": name, "version": pb["version"], "criteria": len(pb["rubric"]),
                             "runs_on_version": ev["runs"]})
    _out(args, rows, "\n".join(f"{r['dept']:<12} {r['name']:<40} v{r['version']}  {r['criteria']} criteria, "
                               f"{r['runs_on_version']} runs on this version" for r in rows) or "no playbooks")


@command("playbook show", "any", "a playbook: its protocol, rubric, and the evidence its next refinement will read",
         arg("name"), arg("--dept", required=True), mutates=False)
def cmd_playbook_show(args, k):
    from holon.capabilities import playbooks
    pb = playbooks.read(k.skills, args.dept, args.name)
    if not pb:
        raise LookupError(f"no playbook {args.name} in {args.dept}")
    ev = k.playbook_evidence(args.name, k.org.dept(args.dept)["id"], pb["version"])
    history = [e for e in k.work.events(f"@{args.dept}/{args.name}", limit=20)]
    text = (f"{args.name} v{pb['version']} ({pb['layer']})\n\n{pb['protocol']}\n\nRubric:\n"
            + "\n".join(f"- {c}" for c in pb["rubric"])
            + f"\n\nOn v{pb['version']}: {ev['runs']} runs, {ev['done']} done, {ev['failed']} failed, "
            f"{len(ev['verify_fails'])} verifier failures, {len(ev['disagreements'])} judge/verifier disagreements\n"
            + "".join(f"  {x['criterion'][:80]}: mean p={x['mean']:.2f} over {x['n']}\n" for x in ev["criteria"])
            + "\nHistory:\n" + "".join(f"  {e['ts'][:16]} {e['from_status'] or ''}->{e['to_status']} {e['note'] or ''}\n"
                                       for e in history))
    _out(args, {**pb, "evidence": ev, "history": history}, text)


@command("classify", "employee", "ask the decision model which lane a job needs (script, fast, full)",
         arg("title"), arg("--intent", required=True, help="text, '-' for stdin, or @file"), arg("--command"),
         mutates=False)
def cmd_classify(args, k):
    c = k.classify(args.title, _text_arg(args.intent), args.command)
    _out(args, c, f"lane: {c['lane'] or 'unsure (full)'}" + (
        f" ({c['confidence']:.0%}; external effects p={c['external_p']})" if c["source"] == "classifier"
        else f" (classifier {c['source']}{': ' + c['error'] if c.get('error') else ''})"))


@command("post-execute", "kernel", "run a handed-in result's machine checks and rubric judge (the heartbeat spawns "
         "this)", arg("work_id"))
def cmd_post_execute(args, k):
    out = k.post_execute(args.work_id)
    _out(args, out, f"{args.work_id}: " + (f"-> {out['work']['status']}" if out.get("work") else str(out or "nothing held")))


@command("run-script", "kernel", "run a ready script-lane stage (the heartbeat spawns this)", arg("stage_id", nargs="?"))
def cmd_run_script(args, k):
    out = k.run_script(args.stage_id)
    if out is None:
        _out(args, {"ran": None}, "no script stage ready")
        return NOTHING
    w = out.get("work") or {}
    _out(args, out, f"script run done; work {w.get('id')} -> {w.get('status')}")


@command("schedule list", "any", "schedules", mutates=False)
def cmd_schedule_list(args, k):
    rows = k.schedules.list()
    _out(args, rows, "\n".join(f"{r['id']}  {r['kind']:<9} '{r['cron']}' {'on ' if r['enabled'] else 'off'} next "
                               f"{r['next_run_at']} {r['employee_id'] or r['template_id']}"
                               + (f" stage={r['stage']}" if r["stage"] else "") for r in rows) or "no schedules")


@command("schedule enable", "chair", "enable a schedule", arg("schedule_id"))
def cmd_schedule_enable(args, k):
    _out(args, k.schedules.set_enabled(args.schedule_id, True), f"{args.schedule_id} enabled")


@command("schedule disable", "chair", "disable a schedule", arg("schedule_id"))
def cmd_schedule_disable(args, k):
    _out(args, k.schedules.set_enabled(args.schedule_id, False), f"{args.schedule_id} disabled")


# ── communication ─────────────────────────────────────────────────────────
def _sender(args, k) -> str:
    me = _me(args, k, required=False)
    return f"emp:{me}" if me else vocab.CHAIR


@command("msg send", "employee", "message a colleague, a department, the organization or the Chair",
         AS, arg("to", help="emp:<id> | dept:<slug> | org | chair"), arg("body"), arg("--task"),
         arg("--literal", action="store_true", help="the body is literal text: a runtime relaying it must not "
             "let '@path' or '-' choose a file"))
def cmd_msg_send(args, k):
    body = args.body if args.literal else _text_arg(args.body)
    mid = k.work.post_message(_sender(args, k), args.to, body,
                              kind="channel" if args.to.startswith(("dept:", "org")) else "dm", task_id=args.task)
    _out(args, {"id": mid}, f"sent {mid} to {args.to}")


@command("msg inbox", "employee", "your messages (and your department's)", AS, mutates=False)
def cmd_msg_inbox(args, k):
    ms = k.inbox(_sender(args, k))
    _out(args, ms, "\n".join(f"{m['ts'][:16]} [{m['kind']}] {m['sender']} → {m['to_ref']}: {m['body'][:160]}"
                             for m in ms) or "inbox empty")


@command("msg request", "employee", "ask another department for work (it becomes work there)",
         AS, arg("dept"), arg("title"), arg("--intent", required=True, help="text, '-' for stdin, or @file"),
         arg("--acceptance", help="text, '-' for stdin, or @file"),
         arg("--service", metavar="unit:service", help="a declared service (K-5): validated against the "
             "bylaws registry, and named in the request so the performer knows which entry point to run"),
         arg("--priority", type=int, default=3, choices=list(vocab.PRIORITIES)))
def cmd_msg_request(args, k):
    title = args.title
    if args.service:
        u, _, s = args.service.rpartition(":")
        if not u or not k.wconn.execute("SELECT 1 FROM unit_services WHERE slug=? AND service=?",
                                       (u, s)).fetchone():
            raise ValueError(f"{args.service!r} is not a declared service (holon bylaws sync; "
                             "api_services lists them)")
        title = f"[{args.service}] {title}"
    t = k.request(_sender(args, k), args.dept, title, _text_arg(args.intent),
                  acceptance=_text_arg(args.acceptance), priority=args.priority)
    _out(args, t, f"request filed as {t['id']} in {args.dept}")


# ── knowledge ─────────────────────────────────────────────────────────────
@command("memory add", "employee", "add to institutional memory",
         AS, arg("--scope", required=True, help="emp:<id> | dept:<slug> | org | <project ref> | class:<name>"),
         arg("--kind", required=True, choices=list(vocab.MEMORY_KINDS)), arg("--content", required=True),
         arg("--provenance"), arg("--confidence", default="medium", choices=list(vocab.CONFIDENCE)),
         arg("--pin", action="store_true"),
         arg("--literal", action="store_true", help="the content is literal text: a runtime relaying it must not "
             "let '@path' or '-' choose a file"))
def cmd_memory_add(args, k):
    p = args.principal
    if p.kind == "employee":
        me = k.org.employee(p.employee_id)
        if args.scope.startswith("emp:") and args.scope != f"emp:{me['id']}":
            raise PermissionError("employees write only their own emp: scope")
        if args.scope.startswith("dept:") and args.scope != f"dept:{me['dept']}":
            raise PermissionError("employees write only their own department's scope")
        if args.scope == "org" or args.pin:
            raise PermissionError("org-scope and pinned memory are the Chair's (promotion does the rest)")
        # R-0, the shared-scope fence: cross-cutting scopes reach work the writer does not own, and
        # steering kinds there read as binding law for every covering run — so an employee may
        # contribute non-steering knowledge only to a class their department performs, and steering
        # kinds (constraint/decision/preference) reach shared scopes only through consolidation,
        # a plan review, or the Chair. (emp:/dept:/org were decided above.)
        shared = args.scope.startswith("class:") or (":" in args.scope and not args.scope.startswith(("emp:", "dept:")))
        if shared and args.kind in vocab.STEERING_KINDS:
            raise PermissionError("steering kinds reach shared scopes only through consolidation or the Chair")
        if shared and args.scope.startswith("class:"):
            classes = {r[0] for r in k.wconn.execute(
                "SELECT DISTINCT task_class FROM tasks WHERE dept_id=(SELECT id FROM departments WHERE slug=?)"
                " AND task_class IS NOT NULL", (me["dept"],))}
            if args.scope[len("class:"):] not in classes:
                raise PermissionError("employees contribute to a class scope only for classes their department performs")
        if shared and not args.scope.startswith("class:"):
            # a project ref: only the scope of work in the writer's own department
            own = k.wconn.execute("SELECT 1 FROM tasks WHERE project_ref=? AND dept_id=(SELECT id FROM departments"
                                  " WHERE slug=?)", (args.scope, me["dept"])).fetchone()
            if not own:
                raise PermissionError("employees write project scopes only for work their department owns")
    it = k.memory.add_item(args.scope, args.kind, args.content if args.literal else _text_arg(args.content),
                           args.provenance or p.actor, confidence=args.confidence, pinned=args.pin)
    _out(args, it, f"{it['id']} {it['action']}")


@command("memory search", "any", "search institutional memory", arg("query"), arg("--scope"),
         arg("--limit", type=int, default=10), mutates=False)
def cmd_memory_search(args, k):
    items = k.memory.search(args.query, scopes=[args.scope] if args.scope else None, limit=args.limit)
    _out(args, items, "\n".join(
        f"{i['id']}  [{i['scope']}/{i['kind']}] {i['content']} (confidence: {i['confidence']}"
        + (f"; provenance: {i['provenance']}" if i.get("provenance") else "") + ")"
        for i in items) or "no matches")


@command("memory episodes", "any", "search past run digests for problems like this", arg("query"),
          arg("--scope"), arg("--limit", type=int, default=10), mutates=False)
def cmd_memory_episodes(args, k):
    eps = k.memory.search_episodes(args.query, scopes=[args.scope] if args.scope else None, limit=args.limit)
    _out(args, eps, "\n".join(
        f"{e['ts'][:16]}  [{e['scope']}] {e['outcome'] or e['scope']} — {e['digest']}"
        + (f" (task:{e['task_id']})" if e.get("task_id") else "") for e in eps) or "no matches")


@command("memory pin", "chair", "pin an item (every run in its scope sees it)", arg("item_id"))
def cmd_memory_pin(args, k):
    it = k.memory.set_pinned(args.item_id, True)
    _out(args, it, f"{it['id']} pinned")


@command("memory unpin", "chair", "unpin an item", arg("item_id"))
def cmd_memory_unpin(args, k):
    it = k.memory.set_pinned(args.item_id, False)
    _out(args, it, f"{it['id']} unpinned")


@command("memory retire", "chair", "retire an item", arg("item_id"))
def cmd_memory_retire(args, k):
    it = k.memory.retire(args.item_id)
    _out(args, it, f"{it['id']} retired")


@command("memory promote", "chair", "promote an item to a wider scope", arg("item_id"),
         arg("--to", required=True, help="dept:<slug> or org"))
def cmd_memory_promote(args, k):
    it = k.memory.get_item(args.item_id)
    up = k.memory.add_item(args.to, it["kind"], it["content"], f"promoted:{it['id']}", confidence="high")
    if up["action"] == "created":
        # a promoted item carries its provenance edge on every path, not only consolidation (KB-6)
        k.memory.link(f"mem:{up['id']}", f"mem:{it['id']}", "derived_from")
    _out(args, up, f"{up['id']} {up['action']} in {args.to}")


@command("memory state", "chair", "read or set a project's checkpoint", arg("scope"), arg("--summary"),
         arg("--focus"), arg("--next"))
def cmd_memory_state(args, k):
    if any(v is not None for v in (args.summary, args.focus, args.next)):
        st = k.memory.update_state(args.scope, summary=args.summary, current_focus=args.focus, next_steps=args.next)
    else:
        st = k.memory.get_state(args.scope)
    _out(args, st)


@command("memory show", "any", "an item: its content, weight, and its provenance graph", arg("item_id"),
         mutates=False)
def cmd_memory_show(args, k):
    it = k.memory.get_item(args.item_id)
    links = {r["rel"]: [] for r in k.mconn.execute(
        "SELECT rel FROM links WHERE from_ref=? OR to_ref=?", (f"mem:{it['id']}",) * 2)}
    for r in k.mconn.execute(
            "SELECT from_ref, to_ref, rel FROM links WHERE from_ref=? OR to_ref=?",
            (f"mem:{it['id']}",) * 2):
        end = r["to_ref"] if r["from_ref"] == f"mem:{it['id']}" else r["from_ref"]
        links.setdefault(r["rel"], []).append(end)
    lineage = [(r[0], r[1], r[2]) for r in k.mconn.execute(
        "WITH RECURSIVE back(id, content, rel) AS ("
        " SELECT superseded_by, content, 'supersedes' FROM items WHERE id=?"
        " UNION ALL SELECT i.superseded_by, i.content, 'supersedes' FROM items i JOIN back b ON i.id=b.id"
        " WHERE i.superseded_by IS NOT NULL) SELECT id, substr(content,1,120), rel FROM back",
        (it["id"],))]
    _out(args, {**it, "links": links, "superseded_chain": lineage},
         f"{it['id']} [{it['scope']}/{it['kind']}] (confidence: {it['confidence']}; uses: {it['uses']}"
         + ("; pinned" if it["pinned"] else "") + ")\n"
         + (it["content"] or "")
         + "\n" + "\n".join(f"{rel}: {', '.join(refs)}" for rel, refs in links.items()))


@command("recall", "any", "one joined answer from the organization's knowledge: memory, episodes, skills, playbooks",
         arg("question"), arg("--limit", type=int, default=6), arg("--dept"), mutates=False)
def cmd_recall(args, k):
    """R-1, the join surface: the pull-mode counterpart of the context bundle. Deterministic and
    budgeted; every source is a read the kernel already owns. `--dept` (defaulted to an employee
    principal's own department) adds the department skill layer to the scan — where a pool's
    playbooks live — so a rubric line is findable without knowing the playbook's name."""
    q, out = args.question, []
    dept = args.dept
    if dept is None and args.principal.kind == "employee":
        dept = k.org.employee(args.principal.employee_id)["dept"]
    items = k.memory.search(q, limit=args.limit)
    if items:
        out.append("## Memory\n\n" + "\n".join(
            f"- [{i['scope']}/{i['kind']}] {i['content']} (mem:{i['id']}; confidence: {i['confidence']}"
            + (f"; provenance: {i['provenance']}" if i.get("provenance") else "") + ")" for i in items))
    eps = k.memory.search_episodes(q, limit=max(3, args.limit // 2))
    if eps:
        out.append("## Past work\n\n" + "\n".join(
            f"- {e['ts'][:10]} [{e['scope']}] {e['digest']}" + (f" (task:{e['task_id']})" if e.get("task_id") else "")
            for e in eps))
    hits = k.skills.search(q, limit=args.limit, dept=dept)
    if hits:
        out.append("## Skills and playbooks (protocol + rubric)\n\n" + "\n".join(f"- {h}" for h in hits))
    gold = []
    try:
        from holon.capabilities.memory import fts_query
        terms = [w.strip('"') for w in fts_query(q).split(" OR ")]
        for r in k.wconn.execute(
                "SELECT w.id AS work_id, o.summary FROM outputs o JOIN tasks w ON w.id=o.task_id"
                " WHERE o.stage='execute' AND o.outcome='pass' AND o.summary IS NOT NULL"
                " ORDER BY o.created_at DESC LIMIT 200"):
            if any(t in (r["summary"] or "").lower() for t in terms):
                gold.append(dict(r))
            if len(gold) >= 3:
                break
    except sqlite3.Error:
        gold = []
    if gold:
        out.append("## Verified results (gold standards)\n\n" + "\n".join(
            f"- {g['work_id']}: {(g['summary'] or '')[:200]}" for g in gold))
    # R-2's sensor: the recall records what it found, and surfaced items gain their use
    k.memory.log_recall(q, items=len(items), episodes=len(eps), skills=len(hits), gold=len(gold))
    k.memory.mark_used([i["id"] for i in items])
    if not out:
        _out(args, [], "no matches in memory, episodes, skills, playbooks or gold standards")
        return
    text = f"# Recall: {q}\n\n" + "\n\n".join(out)
    _out(args, {"markdown": text}, text)


@command("law show", "any", "the distribution's law docs (read-only): intent, bylaws, contract", arg("doc", choices=("intent", "bylaws", "contract")),
         mutates=False)
def cmd_law_show(args, k):
    """R-5: agents stop depending on possibly-stale distilled copies for constitutional questions;
    the served file is the distribution's own doc, so no second copy exists to drift."""
    path = {"intent": "docs/INTENT.md", "bylaws": "docs/BYLAWS.md", "contract": "docs/CONTRACT.md"}[args.doc]
    p = Path(__file__).resolve().parents[1] / path
    if not p.exists():
        raise LookupError(f"the distribution ships no {path}")
    _out(args, {"path": path, "markdown": p.read_text()}, p.read_text())


@command("memory context", "any", "preview the context bundle a stage would receive", AS, arg("task_id"),
         arg("--budget", type=int), mutates=False)
def cmd_memory_context(args, k):
    from holon.capabilities.context import compile_context
    emp = k.org.employee(args.as_) if args.as_ else None
    md, meta = compile_context(k.home, k.work, k.memory, args.task_id,
                               budget=args.budget or int(k.config["context"]["budget_chars"]),
                               recall_floor=int(k.config["context"]["recall_floor_chars"]),
                               employee=emp,
                               predecessors=k.org.predecessors(emp["id"]) if emp else None)
    _out(args, {"markdown": md, **meta}, md)


# ── bylaws and governance ─────────────────────────────────────────────────
@command("charter authority", "any", "who may approve which grade (--set-for: Chair only)",
         arg("--set-for", help="approver to set: chair or emp:<id>"), arg("--task-class", default="*"),
         arg("--max-grade", default="G1"), arg("--reason", default="set by the Chair"))
def cmd_charter_authority(args, k):
    if args.set_for:
        if args.principal.kind != "chair":
            raise PermissionError("only the Chair sets authority")
        r = k.set_authority(args.set_for, args.task_class, int(args.max_grade.upper().lstrip("G")), args.reason)
        if not r["applied"]:
            print(f"widening needs approval: proposal {r['proposal']} -> {r['approver']}", file=sys.stderr)
    rows = k.org.authority_table()
    _out(args, rows, "\n".join(f"{r['approver']:<34} {r['task_class']:<16} up to G{r['max_grade']}  ({r['reason']})"
                               for r in rows))


@command("charter autonomy", "any", "effective autonomy policy per department (holon.toml [autonomy])",
         mutates=False)
def cmd_charter_autonomy(args, k):
    rows = [{"dept": d["slug"], **k.org.policy(d["slug"])} for d in k.org.depts()]
    org = {"dept": "(organization)", **k.org.policy()}
    _out(args, [org] + rows, "\n".join(
        f"{r['dept']:<16} {r['mode']:<11} widen→{r['widen_approver']:<6} earned≤G{r['auto_promote_cap']} "
        f"after {r['promote_after']}  verify trusted G0: {r['verify_sample_rate']:.0%} (trusted after "
        f"{r['trusted_after']})  audit {r['audit_sample_permille'] / 10:.0f}%" for r in [org] + rows))


def _skill_dept(args, k) -> str | None:
    if args.dept:
        return args.dept
    me = _me(args, k, required=False) if "as_" in args.__dict__ else None
    return k.org.employee(me)["dept"] if me else None


@command("skill list", "any", "skills available (an employee's own with --as)", AS, arg("--dept"), arg("--role"),
         mutates=False)
def cmd_skill_list(args, k):
    dept = _skill_dept(args, k)
    rows = k.skills.catalog(dept, args.role)
    me = _me(args, k, required=False)
    if me:
        e = k.org.employee(me)
        mine = {"holon-employee", "holon-collaboration", "holon-memory"} | {f"holon-{r}" for r in e["roles"]} \
            | set(e["skills"] or [])
        rows = [m for m in rows if m["name"] in mine]
    _out(args, rows, "\n".join(f"{m['name']:<24} {m.get('kind', '?'):<10} {m['source']:<14} "
                               f"{','.join(m.get('roles') or [])}\n    {m.get('description', '')[:140]}" for m in rows)
         or "no skills")


@command("skill show", "any", "print a skill file", AS, arg("name"), arg("file", nargs="?", default="SKILL.md"),
         arg("--dept"), mutates=False)
def cmd_skill_show(args, k):
    layer, path = k.skills.resolve(args.name, args.file, _skill_dept(args, k))
    _out(args, {"layer": layer, "path": str(path), "text": path.read_text()}, path.read_text())


@command("skill path", "any", "where a skill file resolves", AS, arg("name"),
         arg("file", nargs="?", default="SKILL.md"), arg("--dept"), mutates=False)
def cmd_skill_path(args, k):
    layer, path = k.skills.resolve(args.name, args.file, _skill_dept(args, k))
    _out(args, {"layer": layer, "path": str(path)}, str(path))


@command("skill invoke", "any", "render a skill for an employee and record the use", AS, arg("name"),
         arg("file", nargs="?", default="SKILL.md"), arg("--dept"))
def cmd_skill_invoke(args, k):
    r = k.invoke_skill(args.name, args.file, _me(args, k, required=False), args.dept)
    _out(args, r, r["text"])


@command("skill fork", "chair", "copy a skill (or one file) into the instance or --dept layer to tailor it",
         arg("name"), arg("--file"), arg("--dept"))
def cmd_skill_fork(args, k):
    written = k.skills.fork(args.name, args.dept, args.file)
    _out(args, [str(p) for p in written], "\n".join(f"forked {p}" for p in written) or "already tailored")


@command("skill new", "chair", "scaffold a new skill in the instance (or --dept) layer", arg("name"),
         arg("--kind", required=True, choices=list(vocab.SKILL_KINDS)), arg("--description", required=True),
         arg("--roles"), arg("--dept"))
def cmd_skill_new(args, k):
    p = k.skills.new(args.name, args.kind, args.description, args.roles.split(",") if args.roles else None, args.dept)
    _out(args, {"path": str(p)}, f"created {p}")


@command("skill check", "any", "validate every skill (frontmatter, links, roles)", arg("--dept"), mutates=False)
def cmd_skill_check(args, k):
    problems = k.skills.check(args.dept, list(vocab.ROLES))
    _out(args, {"problems": problems}, "\n".join(problems) or f"{len(k.skills.names(args.dept))} skills ok")
    return 1 if problems else 0


@command("skill export", "chair", "write resolved, rendered skills to a directory a harness loads skills from",
         arg("--to", required=True), arg("--name", action="append"), arg("--dept"))
def cmd_skill_export(args, k):
    # exported skills serve every instance and run: `$HOLON` is the kernel command each run exports, and
    # pull-mode sessions fall back to `holon` on PATH with HOLON_HOME set
    # registered: when a verified skill change lands, the kernel refreshes this directory too
    out = k.export_skills(Path(args.to).expanduser(), {"holon": EXPORTED_HOLON}, args.name or None, args.dept)
    _out(args, [str(p) for p in out], "\n".join(f"exported {p}" for p in out))


@command("contract schemas", "any", "the result contract as JSON Schemas (--write DIR to publish)",
         arg("--write", metavar="DIR"), arg("--stage", choices=list(vocab.STAGES)), mutates=False)
def cmd_contract_schemas(args, k):
    from holon.core import contract
    stages = [args.stage] if args.stage else list(vocab.STAGES)
    if args.write:
        d = Path(args.write)
        d.mkdir(parents=True, exist_ok=True)
        for s in stages:
            (d / f"{s}-result.schema.json").write_text(contract.schema_text(s))
        _out(args, {"written": [str(d / f"{s}-result.schema.json") for s in stages]}, f"wrote {len(stages)} schemas")
        return
    _out(args, {s: contract.json_schema(s) for s in stages})


# ── integrity ─────────────────────────────────────────────────────────────
@command("audit samples", "any", "employee approvals sampled for the Chair to re-judge",
         arg("--all", action="store_true"), mutates=False)
def cmd_audit_samples(args, k):
    rows = k.audit_samples(pending_only=not args.all)
    _out(args, rows, "\n".join(f"{r['task_id']}  G{r['grade']} approved by {r['approver']}: {r['title']}"
                               + (f"  [{r['verdict']}]" if r["verdict"] else "") for r in rows)
         or "no audit samples pending")


@command("audit judge", "chair", "re-judge a sampled approval", arg("ref"), arg("verdict", choices=["agree", "disagree"]),
         arg("--note", required=True))
def cmd_audit_judge(args, k):
    out = k.judge_sample(args.ref, args.verdict, args.note)
    _out(args, out, f"{out['approval']}: {args.verdict}" + (
        f"; authority {out['authority_change']['from']}→{out['authority_change']['to']}"
        if out["authority_change"] else ""))


@command("events", "any", "the audit trail", arg("--id"), arg("--limit", type=int, default=30), mutates=False)
def cmd_events(args, k):
    ev = k.work.events(args.id, args.limit)
    _out(args, ev, "\n".join(f"{e['ts'][:19]} {e['entity']} {e['entity_id']} {e['from_status'] or '-'} -> "
                             f"{e['to_status'] or '-'} by {e['actor']}" + (f": {e['note']}" if e["note"] else "")
                             for e in ev))


@command("drivers", "any", "available harness drivers", mutates=False)
def cmd_drivers(args, k):
    from holon.execution import drivers
    ds = drivers.available(k.home)
    _out(args, {n: {"source": d.source, "stages": d.stages} for n, d in ds.items()},
         "\n".join(f"{n:<16} {d.source}" for n, d in ds.items()))


@command("scope", "any", "what each department's employees may reach beyond their work's workdir ([scope])",
         arg("--dept"), mutates=False)
def cmd_scope(args, k):
    out = [k.scope(args.dept)] if args.dept else k.scopes()
    lines = [f"{s['name']:<12} roots={s['roots'] or '-'} read_roots={s.get('read_roots') or '-'} "
             f"commands={s['commands'] or '-'} act_commands={s['act_commands'] or '-'}" for s in out]
    _out(args, {"fingerprint": k.scope_fingerprint(), "scopes": out},
         "\n".join(lines) + f"\nfingerprint {k.scope_fingerprint()} (harness sandboxes built from it must match: "
                            "re-run the adapter installer after changing [scope])")


@command("route", "any", "which department's reach owns a path ([scope] roots; the deepest root wins)", arg("path"),
         mutates=False)
def cmd_route(args, k):
    # the writable reach only: a pooled root is its unit owner's work (R-06), so no pooling
    # department "owns" it — writes there are a msg request, never a routed department's
    target = Path(args.path).expanduser().resolve()
    best = None
    for s in k.scopes():
        for r in s["roots"]:
            root = Path(r).resolve()
            if target.is_relative_to(root) and (best is None or len(root.parts) > len(best[1].parts)):
                best = (s["name"], root)
    data = {"path": str(target), "scope": best[0] if best else None, "root": str(best[1]) if best else None}
    _out(args, data, f"{target}: {best[0]} (via {best[1]})" if best else f"{target}: no department's reach covers it")
    return 0 if best else NOTHING


@command("metrics", "any", "the feedback loops' sensors, each with its counter-metric", arg("--dept"),
         arg("--days", type=int, default=30), mutates=False)
def cmd_metrics(args, k):
    m = k.metrics(args.dept, args.days)
    lines = [f"{m['dept']} — last {m['days']} days"]
    for key, v in m.items():
        if isinstance(v, dict) and "value" in v:
            lines.append(f"  {key:<26} {v['value'] if v['value'] is not None else '-':<10} (counter: {v['counter']})")
    lines.append(f"  backlog                    {m['backlog']['ready_stages']} ready, oldest "
                 f"{m['backlog']['oldest_age_h'] or '-'}h")
    if m.get("budget"):
        b = m["budget"]
        lines.append(f"  budget                     running {b['running']}/{b['max_running_per_dept'] or '∞'}, "
                     f"last 24h {b['runs_last_24h']}/{b['max_runs_per_dept_per_day'] or '∞'}")
    lines.append("  cycle time (h) by stage    " + (", ".join(f"{s}={h}" for s, h in m["cycle_time_h_by_stage"].items())
                                                    or "-"))
    _out(args, m, "\n".join(lines))


@command("lint", "any", "check the distribution and instance artifacts: schema, references, bindings, depth, "
                        "redundancy, agnosticism", arg("--pack", action="append", help="only these packs"),
         arg("--strict", action="store_true", help="warnings fail too"), mutates=False)
def cmd_lint(args, k):
    from holon.integrity import lint
    findings = lint.run(k, args.pack)
    errors = [f for f in findings if f["severity"] == "error"]
    warns = [f for f in findings if f["severity"] == "warning"]
    _out(args, {"errors": len(errors), "warnings": len(warns), "findings": findings},
         "\n".join(f"{f['severity'][:4].upper()} {f['rule']:<22} {f['where']}: {f['message']}" for f in findings)
         + f"\n{len(errors)} errors, {len(warns)} warnings")
    return 1 if errors or (args.strict and warns) else 0


@command("doctor", "any", "check the instance's live state: integrity, invariants, audit chain, security",
         mutates=False)
def cmd_doctor(args, k):
    from holon.integrity import doctor
    findings = doctor.run(k)
    errors = [f for f in findings if f["severity"] == "error"]
    _out(args, {"errors": len(errors), "findings": findings},
         "\n".join(f"{f['severity'][:4].upper()} {f['check']:<22} {f['message']}" for f in findings)
         + f"\n{len(errors)} errors, {len(findings) - len(errors)} warnings")
    return 1 if errors else 0


@command("backup", "chair", "a consistent copy of this instance (databases, config, skills, bylaws; never secrets)",
         arg("--to", required=True, metavar="DIR"), arg("--with-runs", action="store_true", help="include run directories"))
def cmd_backup(args, k):
    from holon.integrity import backup
    out = backup.backup(k, args.to, args.with_runs)
    _out(args, out, f"backed up {out['files']} files to {out['path']}")


@command("gc", "chair", "collect what [retention] says is expired: run directories of closed work "
         "(a gold standard's and a judged run's result.json and trajectory.json are kept) and old "
         "messages. Dry run by default; --apply refuses without a recent backup",
         arg("--apply", action="store_true"), arg("--force", action="store_true",
         help="delete even without a recent backup (the Chair's call)"))
def cmd_gc(args, k):
    from holon.integrity import gc
    p = gc.plan(k)
    if not args.apply:
        _out(args, p, f"{len(p['run_dirs'])} run directories, {p['messages']} messages "
                      f"(dry run; --apply collects them)")
        return
    out = gc.apply(k, p, force=args.force)
    _out(args, out, f"removed {out['run_dirs_removed']} run directories "
                    f"({out['run_dirs_trimmed_to_kept_files']} trimmed to their kept files), "
                    f"{out['messages_deleted']} messages")


@command("keys list", "chair", "the Chair key files in the default key dir, and which belong to this "
         "instance -- a scratch instance's key outlives it; deletion is yours, by hand",
         arg("--orphans", action="store_true", help="only the files this instance does not claim"))
def cmd_keys_list(args, k):
    d = Path("~/.config/holon/keys").expanduser()
    mine = Path(k.auth.chair_key_path()).name
    files = sorted(d.glob("*.chair")) if d.is_dir() else []
    rows = [{"file": f.name, "instance": f.stem, "size": f.stat().st_size,
             "mtime": datetime.fromtimestamp(f.stat().st_mtime, timezone.utc).isoformat()[:19],
             "this_instance": f.name == mine} for f in files]
    if args.orphans:
        rows = [r for r in rows if not r["this_instance"]]
    _out(args, rows, "\n".join(f"{r['file']}  {r['size']}B  {r['mtime']}"
                                + ("" if r["this_instance"] else "  (no local instance claims it)")
                                for r in rows) or "no key files")


@command("incident waive", "chair", "record why a closed incident's fix carries no guard (a deliberate "
         "decision, not an omission; `holon doctor` checks for it)", arg("work_id"),
         arg("--note", required=True, help="why this fix needs no re-check"))
def cmd_incident_waive(args, k):
    k.auth._put(f"incident:{args.work_id}:waived", args.note[:400])
    _out(args, {"work": args.work_id, "waived": args.note[:400]},
         f"waived: {args.note[:80]}")


@command("guards", "chair", "the registered incident guards (K-2): what re-checks a fixed defect on "
         "every heartbeat, and the incident each came from")
def cmd_guards(args, k):
    rows = [dict(r) for r in k.wconn.execute(
        "SELECT g.work_id, g.name, g.check, g.timeout, g.created_at, t.title AS incident"
        " FROM guards g LEFT JOIN tasks t ON t.id = g.work_id ORDER BY g.created_at")]
    _out(args, rows, "\n".join(f"{r['name']:<30} {r['check']}  ← {r['incident']}" for r in rows)
         or "no guards registered: an incident fix's `guard` field registers one")


@command("bylaws sync", "chair", "regenerate the units tables from $HOLON_HOME/bylaws/units/*.toml "
         "(the only writer; refuses on a manifest that does not parse)")
def cmd_bylaws_sync(args, k):
    from holon.bylaws import units
    out = units.sync(k)
    _out(args, out, f"{out['units']} units, {out['services']} services")


@command("bylaws emit", "chair", "derive bylaws/registry.md from the synced units (U3): the unit tree, "
         "the services each offers, and the consumption graph")
def cmd_bylaws_emit(args, k):
    from holon.bylaws import units
    if not k.wconn.execute("SELECT 1 FROM units LIMIT 1").fetchone():
        raise ValueError("nothing synced yet: `holon bylaws sync` first")
    p = units.emit(k)
    _out(args, {"path": str(p)}, f"registry written to {p}")


@command("restore", "chair", "restore a backup into this instance (verifies its checksums first)", arg("path"),
         arg("--force", action="store_true", help="required: the instance's current state is replaced"),
         arg("--verify-only", action="store_true"))
def cmd_restore(args, k):
    from holon.integrity import backup
    if args.verify_only:
        r = backup.verify(args.path)
        _out(args, r, "backup intact" if not r["corrupt"] else f"CORRUPT: {r['corrupt']}")
        return 1 if r["corrupt"] else 0
    if not args.force:
        raise ValueError("restore replaces this instance's state; pass --force (take a backup first)")
    out = backup.restore(k, args.path)
    _out(args, out, f"restored from {out['restored_from']}; run `holon doctor`")


@command("adapter test", "chair", "run the harness conformance suite against a driver, or the "
         "supervisor conformance suite against a supervisor command (CONTRACT §12)",
         arg("driver", nargs="?", help="a driver name — omit with --supervisor"),
         arg("--workdir", help="scratch directory (default: a temporary one)"),
         arg("--clauses", help="comma list of clauses to run (default: all)"),
         arg("--supervisor", metavar="CMD", help="test this supervisor command: it reads the prepared "
             "run JSON on stdin, runs the body, renews the lease, enforces the timeout, and hands the "
             "body in (`holon adapter test --supervisor '…'` — the scripted fixture spends no model calls)"))
def cmd_adapter_test(args, k):
    if args.supervisor:
        from holon.integrity import supervisor_conformance
        report = supervisor_conformance.run_supervisor(k, args.supervisor,
                                                        args.clauses.split(",") if args.clauses else None)
    else:
        if not args.driver:
            raise ValueError("a driver name, or --supervisor <cmd>")
        from holon.integrity import conformance
        report = conformance.run(k, args.driver, args.workdir, args.clauses.split(",") if args.clauses else None)
    _out(args, report, "\n".join(f"{'pass' if c['ok'] else 'FAIL'}  {c['clause']:<22} {c['detail']}"
                                 for c in report["clauses"]) + f"\n{report['passed']}/{len(report['clauses'])} passed")
    return 0 if report["passed"] == len(report["clauses"]) else 1


@command("broker", "kernel", "serve the organization to agents running as [isolation] agent_user (a user service)",
         mutates=False)
def cmd_broker(args, k):
    import pwd
    from holon.execution import isolation
    iso = k.isolation
    if not iso:
        raise ValueError("[isolation] agent_user is not set: agents run as this user and need no broker")
    allowed = {pwd.getpwnam(iso["agent_user"]).pw_uid, os.getuid()}
    home = str(k.home.root)
    k.close()
    print(f"holon broker on {iso['broker']} for uids {sorted(allowed)}", flush=True)
    isolation.serve_broker(home, iso["broker"], allowed)


@command("isolation plan", "chair", "the one-time setup that runs agents as [isolation] agent_user: a script for "
         "root and the ACLs this user applies", arg("--write", metavar="FILE", help="write the root script here"),
         mutates=False)
def cmd_isolation_plan(args, k):
    from holon.execution import isolation
    plan = isolation.setup_plan(k)
    if args.write:
        Path(args.write).write_text(plan["root_script"])
    _out(args, plan, (f"1. as root, once: sudo sh {args.write}\n" if args.write else plan["root_script"] + "\n")
         + f"2. as you: holon isolation apply   ({len(plan['acl'])} setfacl commands)\n"
         f"3. start the broker: systemctl --user enable --now holon-broker (install.py writes the unit)\n"
         f"4. holon doctor: the isolation checks probe what {plan['agent_user']} can reach\n\n"
         f"writes: {', '.join(plan['writes']) or '-'}\nreads: {', '.join(plan['reads'])}\nruns: {plan['runs']}")


@command("isolation apply", "chair", "apply the isolation ACLs (agents reach only their trees, run directories "
         "and binaries; every other entry on the way is denied to them)")
def cmd_isolation_apply(args, k):
    from holon.execution import isolation
    plan = isolation.setup_plan(k)
    failed = isolation.apply(plan)
    _out(args, {"applied": len(plan["acl"]) - len(failed), "failed": failed},
         f"applied {len(plan['acl']) - len(failed)} of {len(plan['acl'])}" + "".join(f"\nFAILED {f}" for f in failed))
    return 1 if failed else 0


@command("mcp", "any", "serve the organization as MCP tools over stdio (for MCP-capable harnesses)", mutates=False)
def cmd_mcp(args, k):
    from holon import mcp
    k.close()  # each tool call opens its own kernel, authenticated per call
    mcp.serve()


@command("help", "any", "this command registry (--json: the manifest agents read)", mutates=False)
def cmd_help(args, k):
    m = manifest()
    _out(args, m, "\n".join(f"{c['command']:<22} [{c['actor']:<8}] {c['help']}" for c in m))


# ── knowledge bases (holon/capabilities/kb.py; plan docs/plans/2026-10-19-holon-knowledge-layer.md) ──
@command("kb seed", "chair", "create a knowledge base from a blueprint, bound to one department",
         arg("blueprint", help="a catalog or built-in blueprint name"), arg("slug"),
         arg("--dept", required=True, help="the department slug or id"))
def cmd_kb_seed(args, k):
    from holon.capabilities import kb
    r = kb.seed(k, args.principal, args.blueprint, args.slug, args.dept)
    _out(args, r, f"seeded kb/{r['slug']} from {r['blueprint']}, bound to dept {r['dept']} ({r['commit']})")


@command("kb list", "any", "the knowledge bases you can see", mutates=False)
def cmd_kb_list(args, k):
    from holon.capabilities import kb
    rows = kb.listing(k, args.principal)
    _out(args, rows, "\n".join(f"{s:<24} dept {e['dept']:<14} {e['blueprint']}" for s, e in sorted(rows.items()))
         or "no knowledge bases")


@command("kb run", "any", "one genome verb in a knowledge base: kb run SLUG VERB [ARGS...]",
         arg("slug"), arg("verb", help="find, view, ask, wake, show, lint, new, move, set or link"),
         arg("tokens", nargs=argparse.REMAINDER, help="the verb's own arguments"))
def cmd_kb_run(args, k):
    from holon.capabilities import kb
    print(kb.run(k, args.principal, args.slug, args.verb, args.tokens), end="")


@command("kb blueprint draft", "employee", "draft a blueprint from a base; it seeds nothing until the Chair promotes it",
         arg("name"), arg("--base", required=True, help="the built-in or catalog blueprint to start from"))
def cmd_kb_draft(args, k):
    from holon.capabilities import kb
    r = kb.draft(k, args.principal, args.name, args.base)
    _out(args, r, f"drafted {r['draft']}: {'passes' if r['pass'] else 'fails'} blueprint check ({r['message']})")


@command("kb blueprint check", "employee", "genome blueprint check on a draft or a catalog blueprint",
         arg("name"), mutates=False)
def cmd_kb_check(args, k):
    from holon.capabilities import kb
    r = kb.check(k, args.principal, args.name)
    _out(args, r, f"{r['blueprint']}: {'passes' if r['pass'] else 'fails'} ({r['message']})")


@command("kb blueprint promote", "chair", "promote a passing draft into the catalog: the Chair's ruling", arg("name"))
def cmd_kb_promote(args, k):
    from holon.capabilities import kb
    r = kb.promote(k, args.principal, args.name)
    _out(args, r, f"promoted {r['promoted']} to the catalog")


# ── parser and dispatch ───────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="holon", description="Holon: an agentic OS that runs as an organization")
    p.add_argument("--home", help="instance root (default: $HOLON_HOME)")
    p.add_argument("--token", help="a run token (default: $HOLON_RUN_TOKEN)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"holon {__version__}")
    root = p.add_subparsers(dest="cmd", required=True)
    groups: dict[tuple, argparse._SubParsersAction] = {(): root}
    for c in REGISTRY:
        parent = groups[()]
        for i in range(len(c.path) - 1):
            key = c.path[: i + 1]
            if key not in groups:
                gp = groups[key[:-1]].add_parser(key[-1], help=f"{key[-1]} commands")
                groups[key] = gp.add_subparsers(dest="_".join(key) + "_cmd", required=True)
            parent = groups[key]
        sp = parent.add_parser(c.path[-1], help=c.help, description=c.help)
        for a, kw in c.args:
            sp.add_argument(*a, **kw)
        sp.set_defaults(_command=c)
    return p


def _allowed(cmd: Command, principal) -> bool:
    if principal.kind == "chair":
        return True
    if cmd.actor == "any":
        return True
    return cmd.actor == "employee" and principal.kind == "employee"


def main(argv: list[str] | None = None, broker: dict | None = None) -> int:
    """`broker`: set by the broker (isolation.py) for an agent's request: who it is comes only from its
    token or key, only agent commands run, and file arguments come from the request."""
    global _FILES
    from holon.execution.security import AuthError
    args = build_parser().parse_args(argv)
    cmd: Command = args._command
    if broker is None and os.environ.get("HOLON_BROKER"):  # an agent under isolation: the broker decides
        if cmd.path == ("mcp",):
            from holon import mcp
            mcp.serve()
            return 0
        from holon.execution import isolation
        return isolation.forward(list(sys.argv[1:] if argv is None else argv), os.environ["HOLON_BROKER"])
    if broker is not None:
        from holon.mcp import AGENT_COMMANDS
        if " ".join(cmd.path) not in (*AGENT_COMMANDS, *BROKER_EXTRA):
            print(f"holon: `{' '.join(cmd.path)}` is not available to agents", file=sys.stderr)
            return FORBIDDEN
        _FILES = broker.get("files") or {}
    k = None
    try:
        from holon.execution.service import Kernel
        home = Home.resolve(args.home)
        k = Kernel(home, actor="anonymous")
        principal = k.auth.agent_principal(broker.get("token"), broker.get("key")) if broker is not None \
            else k.auth.principal(args.token)
        if principal.legacy and cmd.mutates and cmd.path != ("init",):
            print("holon: authentication is not configured (open mode); run `holon init` to create the Chair key",
                  file=sys.stderr)
        if not _allowed(cmd, principal):
            print(f"holon: `{' '.join(cmd.path)}` is for the {cmd.actor} actor class (you are {principal.kind})",
                  file=sys.stderr)
            return FORBIDDEN
        args.principal = principal
        k.actor = k.work.actor = k.org.actor = k.schedules.work.actor = principal.actor
        return cmd.fn(args, k) or 0
    except HomeNotSet as e:
        print(f"holon: {e}", file=sys.stderr)
        return 2
    except (PermissionError, AuthError) as e:
        print(f"holon: not allowed: {e}", file=sys.stderr)
        return FORBIDDEN
    except (LookupError, ValueError, RuntimeError, OSError) as e:
        if args.json:
            print(json.dumps({"error": type(e).__name__, "message": str(e)}))
        else:
            print(f"holon: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    finally:
        _FILES = None
        if k is not None:
            k.close()
