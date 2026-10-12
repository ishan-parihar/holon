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
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from holon import __version__
from holon.core import vocab
from holon.core.paths import Home, HomeNotSet

NOTHING, FORBIDDEN = 3, 4
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


def _text_arg(v: str | None) -> str | None:
    """'-' reads stdin; '@path' reads a file; anything else is literal."""
    if v == "-":
        return sys.stdin.read()
    if v and v.startswith("@"):
        return Path(v[1:]).expanduser().read_text()
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
            "# holon instance config (docs/GENOME.md, docs/CONTRACT.md)\n[context]\nbudget_chars = 24000\n\n"
            "[planner]\nenforce_read_only = true\ntrust_harness_read_only = false\n\n"
            "[grading]\nnovelty_min_verified = 3\nsensitive_paths = []\n\n"
            "[autonomy]\n# supervised (safe default) | delegated | autonomous; keys below override the preset\n"
            "mode = \"supervised\"\n# verify_sample_rate = 0.2\n# [autonomy.dept.<slug>]\n# mode = \"delegated\"\n\n"
            "[memory]\npromotion_threshold = 2\n")
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


@command("dept create", "chair", "create a department from a genome profile",
         arg("slug"), arg("--name", required=True), arg("--mission", help="text, '-' for stdin, or @file"),
         arg("--parent", help="parent department slug (default: executive)"),
         arg("--profile", default="light", help="light | standard | <instance profile> | file.toml"))
def cmd_dept_create(args, k):
    from holon.execution import genome
    d = k.org.create_dept(args.slug, args.name, _text_arg(args.mission), args.parent,
                          genome.load_profile(args.profile, k.home))
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


@command("dept profile", "chair", "change a department's genome profile", arg("slug"), arg("profile"),
         arg("--reason", required=True))
def cmd_dept_profile(args, k):
    from holon.execution import genome
    d = k.org.set_profile(args.slug, genome.load_profile(args.profile, k.home), args.reason)
    _out(args, d, f"{d['slug']} now follows profile {d['profile']}")


@command("dept profiles", "any", "available genome profiles", mutates=False)
def cmd_dept_profiles(args, k):
    from holon.execution import genome
    out = [genome.load_profile(n, k.home) for n in sorted(genome.profile_paths(k.home))]
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
         arg("--due"), arg("--max-attempts", type=int, default=2))
def cmd_task_add(args, k):
    t = k.work.add_work(args.title, _text_arg(args.intent), acceptance=_text_arg(args.acceptance),
                        project_ref=args.project, parent_id=args.parent, needs=args.after, dept=args.dept,
                        assignee=args.assign, priority=args.priority, source=vocab.CHAIR, task_class=args.task_class,
                        risk=args.risk, workdir=str(Path(args.workdir).expanduser().resolve()) if args.workdir else None,
                        due=args.due, max_attempts=args.max_attempts)
    stage = k.work.stages(t["id"])
    _out(args, t, _line(t) + (f"\n  first stage: {stage[0]['stage']} {stage[0]['id']}" if stage else ""))


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
    env = json.loads(Path(args.file).read_text()) if args.file else None
    out = k.submit(args.run_id, env)
    w = out.get("work") or {}
    msg = f"run {args.run_id} ({out['stage']}) ingested; work {w.get('id')} -> {w.get('status')}"
    if out.get("approval"):
        msg += f"\nplan graded {GRADE[out['approval']['grade']]} → {out['approval']['approver']}"
    if out.get("breach"):
        msg += "\ncontract breach: " + "; ".join(out["breach"])
    for p in (out.get("memory") or {}).get("promotions", []):
        msg += f"\nlesson promoted to {p['to']}: {p['item']}"
    _out(args, out, msg)


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


@command("verify", "chair", "verify a result yourself", arg("task_id"), arg("verdict", choices=["pass", "fail"]),
         arg("--note", required=True))
def cmd_verify(args, k):
    out = k.verify(args.task_id, args.verdict, args.note)
    _out(args, out, f"{out['work']['id']} -> {out['work']['status']}")


@command("dispatch", "kernel", "claim a stage for an employee and run its harness driver", AS,
         arg("--stage", choices=list(vocab.STAGES)), arg("--task"), arg("--schedule", help=argparse.SUPPRESS))
def cmd_dispatch(args, k):
    if not args.as_:
        raise ValueError("--as <emp_id> is required")
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
         AS, arg("to", help="emp:<id> | dept:<slug> | org | chair"), arg("body"), arg("--task"))
def cmd_msg_send(args, k):
    mid = k.work.post_message(_sender(args, k), args.to, _text_arg(args.body),
                              kind="channel" if args.to.startswith(("dept:", "org")) else "dm", task_id=args.task)
    _out(args, {"id": mid}, f"sent {mid} to {args.to}")


@command("msg inbox", "employee", "your messages (and your department's)", AS, mutates=False)
def cmd_msg_inbox(args, k):
    ms = k.inbox(_sender(args, k))
    _out(args, ms, "\n".join(f"{m['ts'][:16]} [{m['kind']}] {m['sender']} → {m['to_ref']}: {m['body'][:160]}"
                             for m in ms) or "inbox empty")


@command("msg request", "employee", "ask another department for work (it becomes work there)",
         AS, arg("dept"), arg("title"), arg("--intent", required=True), arg("--acceptance"),
         arg("--priority", type=int, default=3, choices=list(vocab.PRIORITIES)))
def cmd_msg_request(args, k):
    t = k.request(_sender(args, k), args.dept, args.title, _text_arg(args.intent),
                  acceptance=_text_arg(args.acceptance), priority=args.priority)
    _out(args, t, f"request filed as {t['id']} in {args.dept}")


# ── knowledge ─────────────────────────────────────────────────────────────
@command("memory add", "employee", "add to institutional memory",
         AS, arg("--scope", required=True, help="emp:<id> | dept:<slug> | org | <project ref> | class:<name>"),
         arg("--kind", required=True, choices=list(vocab.MEMORY_KINDS)), arg("--content", required=True),
         arg("--provenance"), arg("--confidence", default="medium", choices=list(vocab.CONFIDENCE)),
         arg("--pin", action="store_true"))
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
    it = k.memory.add_item(args.scope, args.kind, _text_arg(args.content), args.provenance or p.actor,
                           confidence=args.confidence, pinned=args.pin)
    _out(args, it, f"{it['id']} {it['action']}")


@command("memory search", "any", "search institutional memory", arg("query"), arg("--scope"),
         arg("--limit", type=int, default=10), mutates=False)
def cmd_memory_search(args, k):
    items = k.memory.search(args.query, scopes=[args.scope] if args.scope else None, limit=args.limit)
    _out(args, items, "\n".join(f"{i['id']}  [{i['scope']}/{i['kind']}] {i['content']}" for i in items) or "no matches")


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
    _out(args, up, f"{up['id']} {up['action']} in {args.to}")


@command("memory state", "chair", "read or set a project's checkpoint", arg("scope"), arg("--summary"),
         arg("--focus"), arg("--next"))
def cmd_memory_state(args, k):
    if any(v is not None for v in (args.summary, args.focus, args.next)):
        st = k.memory.update_state(args.scope, summary=args.summary, current_focus=args.focus, next_steps=args.next)
    else:
        st = k.memory.get_state(args.scope)
    _out(args, st)


@command("memory context", "any", "preview the context bundle a stage would receive", AS, arg("task_id"),
         arg("--budget", type=int), mutates=False)
def cmd_memory_context(args, k):
    from holon.capabilities.context import compile_context
    emp = k.org.employee(args.as_) if args.as_ else None
    md, meta = compile_context(k.home, k.work, k.memory, args.task_id,
                               budget=args.budget or int(k.config["context"]["budget_chars"]), employee=emp,
                               predecessors=k.org.predecessors(emp["id"]) if emp else None)
    _out(args, {"markdown": md, **meta}, md)


# ── genome and governance ─────────────────────────────────────────────────
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
    lines = [f"{s['name']:<12} roots={s['roots'] or '-'} commands={s['commands'] or '-'} "
             f"act_commands={s['act_commands'] or '-'}" for s in out]
    _out(args, {"fingerprint": k.scope_fingerprint(), "scopes": out},
         "\n".join(lines) + f"\nfingerprint {k.scope_fingerprint()} (harness sandboxes built from it must match: "
                            "re-run the adapter installer after changing [scope])")


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


@command("backup", "chair", "a consistent copy of this instance (databases, config, skills, genome; never secrets)",
         arg("--to", required=True, metavar="DIR"), arg("--with-runs", action="store_true", help="include run directories"))
def cmd_backup(args, k):
    from holon.integrity import backup
    out = backup.backup(k, args.to, args.with_runs)
    _out(args, out, f"backed up {out['files']} files to {out['path']}")


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


@command("adapter test", "chair", "run the harness conformance suite against a driver", arg("driver"),
         arg("--workdir", help="scratch directory (default: a temporary one)"),
         arg("--clauses", help="comma list of clauses to run (default: all)"))
def cmd_adapter_test(args, k):
    from holon.integrity import conformance
    report = conformance.run(k, args.driver, args.workdir, args.clauses.split(",") if args.clauses else None)
    _out(args, report, "\n".join(f"{'pass' if c['ok'] else 'FAIL'}  {c['clause']:<22} {c['detail']}"
                                 for c in report["clauses"]) + f"\n{report['passed']}/{len(report['clauses'])} passed")
    return 0 if report["passed"] == len(report["clauses"]) else 1


@command("mcp", "any", "serve the organization as MCP tools over stdio (for MCP-capable harnesses)", mutates=False)
def cmd_mcp(args, k):
    from holon import mcp
    k.close()  # each tool call opens its own kernel, authenticated per call
    mcp.serve()


@command("help", "any", "this command registry (--json: the manifest agents read)", mutates=False)
def cmd_help(args, k):
    m = manifest()
    _out(args, m, "\n".join(f"{c['command']:<22} [{c['actor']:<8}] {c['help']}" for c in m))


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


def main(argv: list[str] | None = None) -> int:
    from holon.execution.security import AuthError
    args = build_parser().parse_args(argv)
    cmd: Command = args._command
    k = None
    try:
        from holon.execution.service import Kernel
        home = Home.resolve(args.home)
        k = Kernel(home, actor="anonymous")
        principal = k.auth.principal(args.token)
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
        if k is not None:
            k.close()
