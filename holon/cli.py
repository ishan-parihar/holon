"""`holon`: the single entry point to the organization. Human-readable by default, `--json` for agents.

Exit codes: 0 ok · 1 error · 2 usage · 3 nothing to do (e.g. no claimable work).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from holon import __version__
from holon.core.paths import Home, HomeNotSet

NOTHING = 3


def _out(args, data, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        print(text if text is not None else json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _task_line(t: dict) -> str:
    return f"{t['id']}  [{t['status']}] p{t['priority']}  {t['title']}" + (
        f"  — {t['blocked_reason']}" if t.get("blocked_reason") else "")


def _kernel(args):
    from holon.execution.service import Kernel
    return Kernel(Home.resolve(args.home), actor=args.actor)


# ── commands ──────────────────────────────────────────────────────────────
def cmd_init(args):
    k = _kernel(args)
    if not k.home.config_file.exists():
        k.home.config_file.write_text(
            "# holon instance config\n[context]\nbudget_chars = 24000\n\n[planner]\nenforce_read_only = true\n")
    _out(args, {"home": str(k.home.root), "work_db": str(k.home.work_db), "memory_db": str(k.home.memory_db)},
         f"holon initialized at {k.home.root}")


def cmd_status(args):
    k = _kernel(args)
    counts = k.work.counts()
    queue = k.work.awaiting_approval()
    needs = k.work.list_tasks(status="needs_input")
    data = {"tasks": counts, "awaiting_approval": len(queue), "needs_input": len(needs),
            "agents": len(k.roster.list_agents(status="active")), "schedules": len(k.roster.list_schedules())}
    lines = ["tasks: " + (", ".join(f"{s}={n}" for s, n in sorted(counts.items())) or "none"),
             f"awaiting your approval: {len(queue)}", f"needs your input: {len(needs)}",
             f"active agents: {data['agents']}  schedules: {data['schedules']}"]
    _out(args, data, "\n".join(lines))


def cmd_task(args):
    k = _kernel(args)
    w = k.work
    if args.task_cmd == "add":
        intent = args.intent if args.intent != "-" else sys.stdin.read()
        t = w.add_task(args.title, intent, acceptance=args.acceptance, project_ref=args.project, parent_id=args.parent,
                       priority=args.priority, source=args.source, task_class=args.task_class, risk=args.risk,
                       workdir=str(Path(args.workdir).expanduser().resolve()) if args.workdir else None,
                       due=args.due, max_attempts=args.max_attempts)
        _out(args, t, _task_line(t))
    elif args.task_cmd == "list":
        ts = w.list_tasks(status=args.status, project_ref=args.project, limit=args.limit)
        _out(args, ts, "\n".join(_task_line(t) for t in ts) or "no tasks")
    elif args.task_cmd == "show":
        t = w.get_task(args.task_id)
        data = {"task": t, "plans": w.plans_for(t["id"]), "result": w.latest_result(t["id"]),
                "children": w.list_tasks(parent_id=t["id"]), "messages": w.messages(t["id"]),
                "events": w.events(t["id"], limit=20)}
        lines = [_task_line(t), "", t["intent"]]
        if t.get("acceptance"):
            lines += ["", f"acceptance: {t['acceptance']}"]
        for p in data["plans"]:
            lines.append(f"\nplan v{p['version']} {p['id']} [{p['status']}]" +
                         (f" — review: {p['review_note']}" if p["review_note"] else ""))
        if data["result"]:
            r = data["result"]
            lines += ["", f"result [{r['outcome']}] verified={r['verified']}: {r['summary']}"]
        for c in data["children"]:
            lines.append("  child " + _task_line(c))
        _out(args, data, "\n".join(lines))
    elif args.task_cmd == "cancel":
        _out(args, w.cancel(args.task_id, args.note), f"cancelled {args.task_id}")
    elif args.task_cmd == "retry":
        _out(args, w.retry(args.task_id), f"{args.task_id} -> approved (retry)")
    elif args.task_cmd == "replan":
        _out(args, w.replan(args.task_id, args.note or "operator replan"), f"{args.task_id} -> inbox (replan)")
    elif args.task_cmd == "answer":
        _out(args, k.answer(args.task_id, args.text), f"{args.task_id} -> inbox with your answer")


def _plan_text(p: dict) -> str:
    steps = p["steps"]
    try:
        parsed = json.loads(steps)
        if isinstance(parsed, list):
            steps = "\n".join(f"  {s if isinstance(s, str) else json.dumps(s)}" for s in parsed)
    except (json.JSONDecodeError, TypeError):
        pass
    parts = [f"{p['id']}  v{p['version']} [{p['status']}] kind={p['kind']}  task={p['task_id']}",
             f"\nfindings:\n  {p['findings']}", f"\nsteps:\n{steps}"]
    for k in ("writes", "risks", "rollback", "estimate"):
        if p.get(k):
            parts.append(f"\n{k}: {p[k]}")
    return "\n".join(parts)


def cmd_plan(args):
    k = _kernel(args)
    if args.plan_cmd in ("list", "queue"):
        q = k.work.awaiting_approval()
        _out(args, q, "\n".join(f"{p['id']}  p{p['priority']} {p['risk']:<6} v{p['version']} {p['kind']:<9} "
                                f"{p['task_title']}" for p in q) or "approval queue is empty")
    elif args.plan_cmd == "show":
        p = k.work.get_plan(args.plan_id)
        _out(args, p, _plan_text(p))
    elif args.plan_cmd == "approve":
        out = k.approve(args.plan_id, args.note, recurring=args.recurring)
        extra = f" ({len(out['children'])} sub-tasks created)" if out["children"] else ""
        _out(args, out, f"approved {args.plan_id}{extra}; task {out['task']['id']} -> {out['task']['status']}")
    elif args.plan_cmd == "reject":
        out = k.reject(args.plan_id, args.note)
        _out(args, out, f"rejected {args.plan_id}; task {out['task']['id']} -> inbox for replanning")


def cmd_claim(args):
    k = _kernel(args)
    c = k.claim(args.role, args.agent, args.driver, args.task)
    if c is None:
        _out(args, {"claimed": None}, f"nothing to claim for {args.role}")
        return NOTHING
    data = {"run_id": c.run["id"], "task_id": c.task["id"], "brief_dir": str(c.brief_dir),
            "brief_md": str(c.brief_dir / "BRIEF.md"), "result_path": str(c.brief_dir / "result.json"),
            "lease_until": c.run["lease_until"]}
    _out(args, data, f"claimed {c.task['id']} as {args.role}: run {c.run['id']}\n"
                     f"read  {data['brief_md']}\nwrite {data['result_path']}\nthen  holon submit {c.run['id']}")


def cmd_submit(args):
    k = _kernel(args)
    env = json.loads(Path(args.file).read_text()) if args.file else None
    out = k.submit(args.run_id, env)
    t = out.get("task", {})
    msg = f"run {args.run_id} ingested; task {t.get('id')} -> {t.get('status')}"
    if out.get("breach"):
        msg += "\ncontract breach: " + "; ".join(out["breach"])
    _out(args, out, msg)


def cmd_run(args):
    k = _kernel(args)
    if args.run_cmd == "heartbeat":
        r = k.work.heartbeat(args.run_id)
        _out(args, r, f"lease extended to {r['lease_until']}")
    elif args.run_cmd == "show":
        r = k.work.get_run(args.run_id)
        _out(args, {"run": r, "result": k.work.result_for_run(args.run_id)})


def cmd_verify(args):
    k = _kernel(args)
    t = k.work.verify(args.task_id, args.verdict, args.note)
    _out(args, t, f"{args.task_id} -> {t['status']}")


def cmd_dispatch(args):
    k = _kernel(args)
    try:
        out = k.dispatch(args.role, args.agent, args.task)
    except Exception:
        if args.schedule:
            k.roster.record_schedule_outcome(args.schedule, False)
        raise
    if args.schedule:
        k.roster.record_schedule_outcome(args.schedule, True)
    if out is None:
        _out(args, {"dispatched": None}, f"nothing to do for {args.role} {args.agent}")
        return NOTHING
    t = out.get("task", {})
    _out(args, out, f"{args.role} run done; task {t.get('id')} -> {t.get('status')}")


def cmd_tick(args):
    k = _kernel(args)
    out = k.tick(foreground=args.foreground)
    _out(args, out, f"swept {len(out['swept'])} expired leases; fired {len(out['fired'])} schedules")


def cmd_agent(args):
    k = _kernel(args)
    r = k.roster
    if args.agent_cmd == "add":
        a = r.add_agent(args.role, args.driver, agent_id=args.id, max_concurrency=args.max_concurrency,
                        task_classes=args.task_classes.split(",") if args.task_classes else None)
        _out(args, a, f"{a['id']}  {a['role']} via {a['driver']}")
    elif args.agent_cmd == "list":
        ags = r.list_agents(role=args.role)
        _out(args, ags, "\n".join(f"{a['id']}  {a['role']:<9} {a['driver']:<14} {a['status']}" for a in ags)
             or "no agents")
    elif args.agent_cmd in ("pause", "resume", "retire"):
        status = {"pause": "paused", "resume": "active", "retire": "retired"}[args.agent_cmd]
        a = r.set_agent_status(args.agent_id, status, args.reason)
        _out(args, a, f"{a['id']} -> {status}")


def cmd_schedule(args):
    k = _kernel(args)
    r = k.roster
    if args.schedule_cmd == "add":
        s = r.add_schedule(args.kind, args.cron, agent_id=args.agent, plan_id=args.plan)
        _out(args, s, f"{s['id']}  {s['kind']} '{s['cron']}' next {s['next_run_at']}")
    elif args.schedule_cmd == "list":
        ss = r.list_schedules()
        _out(args, ss, "\n".join(f"{s['id']}  {s['kind']:<9} '{s['cron']}' {'on ' if s['enabled'] else 'off'} "
                                 f"next {s['next_run_at']} {s['agent_id'] or s['plan_id']}" for s in ss)
             or "no schedules")
    elif args.schedule_cmd in ("enable", "disable"):
        s = r.set_schedule_enabled(args.schedule_id, args.schedule_cmd == "enable")
        _out(args, s, f"{s['id']} {args.schedule_cmd}d")


def cmd_memory(args):
    k = _kernel(args)
    m = k.memory
    if args.memory_cmd == "add":
        it = m.add_item(args.scope, args.kind, args.content, args.provenance or args.actor,
                        confidence=args.confidence, pinned=args.pin)
        _out(args, it, f"{it['id']} {it['action']}")
    elif args.memory_cmd == "search":
        items = m.search(args.query, scopes=[args.scope] if args.scope else None, limit=args.limit)
        _out(args, items, "\n".join(f"{i['id']}  [{i['scope']}/{i['kind']}] {i['content']}" for i in items)
             or "no matches")
    elif args.memory_cmd in ("pin", "unpin"):
        it = m.set_pinned(args.item_id, args.memory_cmd == "pin")
        _out(args, it, f"{it['id']} pinned={it['pinned']}")
    elif args.memory_cmd == "retire":
        it = m.retire(args.item_id)
        _out(args, it, f"{it['id']} retired")
    elif args.memory_cmd == "state":
        if any(v is not None for v in (args.summary, args.focus, args.next)):
            st = m.update_state(args.scope, summary=args.summary, current_focus=args.focus, next_steps=args.next)
        else:
            st = m.get_state(args.scope)
        _out(args, st)
    elif args.memory_cmd == "context":
        from holon.capabilities.context import compile_context
        md, meta = compile_context(k.home, k.work, m, args.task_id, args.role,
                                   budget=args.budget or int(k.config["context"]["budget_chars"]))
        _out(args, {"markdown": md, **meta}, md)


def cmd_events(args):
    k = _kernel(args)
    ev = k.work.events(args.id, args.limit)
    _out(args, ev, "\n".join(f"{e['ts'][:19]} {e['entity']} {e['entity_id']} {e['from_status'] or '-'} -> "
                             f"{e['to_status'] or '-'} by {e['actor']}" + (f": {e['note']}" if e["note"] else "")
                             for e in ev))


def cmd_driver(args):
    from holon.execution import drivers
    home = Home.resolve(args.home)
    ds = drivers.available(home)
    _out(args, {n: d.source for n, d in ds.items()}, "\n".join(f"{n:<16} {d.source}" for n, d in ds.items()))


# ── parser ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="holon", description="holon kernel")
    p.add_argument("--home", help="instance root (default: $HOLON_HOME)")
    p.add_argument("--actor", default=os.environ.get("HOLON_ACTOR", "human:cli"), help="who is acting (audit trail)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"holon {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init", help="create the instance layout and databases").set_defaults(fn=cmd_init)
    sub.add_parser("status", help="pipeline overview").set_defaults(fn=cmd_status)

    t = sub.add_parser("task", help="intake and operator controls").add_subparsers(dest="task_cmd", required=True)
    a = t.add_parser("add")
    a.add_argument("title")
    a.add_argument("--intent", required=True, help="the outcome wanted ('-' reads stdin)")
    a.add_argument("--acceptance")
    a.add_argument("--project", help="project ref, e.g. traj:<node-id>")
    a.add_argument("--parent")
    a.add_argument("--priority", type=int, default=3)
    a.add_argument("--source", default="human", choices=["human", "observatory", "agent", "schedule"])
    a.add_argument("--task-class")
    a.add_argument("--risk", default="normal", choices=["low", "normal", "high", "irreversible"])
    a.add_argument("--workdir")
    a.add_argument("--due")
    a.add_argument("--max-attempts", type=int, default=2)
    ls = t.add_parser("list")
    ls.add_argument("--status", help="comma-separated")
    ls.add_argument("--project")
    ls.add_argument("--limit", type=int, default=50)
    t.add_parser("show").add_argument("task_id")
    c = t.add_parser("cancel")
    c.add_argument("task_id")
    c.add_argument("--note", required=True)
    t.add_parser("retry").add_argument("task_id")
    rp = t.add_parser("replan")
    rp.add_argument("task_id")
    rp.add_argument("--note")
    an = t.add_parser("answer", help="answer a planner's question (needs_input)")
    an.add_argument("task_id")
    an.add_argument("text")
    for x in (a, ls, c, rp, an):
        x.set_defaults(fn=cmd_task)
    for name in ("show", "retry"):
        t.choices[name].set_defaults(fn=cmd_task)

    pl = sub.add_parser("plan", help="the approval queue").add_subparsers(dest="plan_cmd", required=True)
    pl.add_parser("list").set_defaults(fn=cmd_plan)
    pl.add_parser("queue").set_defaults(fn=cmd_plan)
    s = pl.add_parser("show")
    s.add_argument("plan_id")
    s.set_defaults(fn=cmd_plan)
    ap = pl.add_parser("approve")
    ap.add_argument("plan_id")
    ap.add_argument("--note")
    ap.add_argument("--recurring", action="store_true", help="approve as a reusable template for a recurring schedule")
    ap.set_defaults(fn=cmd_plan)
    rj = pl.add_parser("reject")
    rj.add_argument("plan_id")
    rj.add_argument("--note", required=True, help="what the planner must change (it learns from this)")
    rj.set_defaults(fn=cmd_plan)

    cl = sub.add_parser("claim", help="pull mode: take one unit of work as an agent")
    cl.add_argument("--role", required=True, choices=["planner", "executor", "verifier"])
    cl.add_argument("--agent")
    cl.add_argument("--driver")
    cl.add_argument("--task")
    cl.set_defaults(fn=cmd_claim)
    sm = sub.add_parser("submit", help="pull mode: hand back result.json for a claimed run")
    sm.add_argument("run_id")
    sm.add_argument("--file", help="envelope file (default: the run's result.json)")
    sm.set_defaults(fn=cmd_submit)

    rn = sub.add_parser("run", help="run leases").add_subparsers(dest="run_cmd", required=True)
    rn.add_parser("heartbeat").add_argument("run_id")
    rn.add_parser("show").add_argument("run_id")
    for name in ("heartbeat", "show"):
        rn.choices[name].set_defaults(fn=cmd_run)

    v = sub.add_parser("verify", help="human verification of a result")
    v.add_argument("task_id")
    v.add_argument("verdict", choices=["pass", "fail"])
    v.add_argument("--note", required=True)
    v.set_defaults(fn=cmd_verify)

    d = sub.add_parser("dispatch", help="push mode: claim one unit and run it through the agent's driver")
    d.add_argument("--role", required=True, choices=["planner", "executor", "verifier"])
    d.add_argument("--agent", required=True)
    d.add_argument("--task")
    d.add_argument("--schedule", help=argparse.SUPPRESS)
    d.set_defaults(fn=cmd_dispatch)
    tk = sub.add_parser("tick", help="sweep leases and fire due schedules (call from any timer)")
    tk.add_argument("--foreground", action="store_true", help="run dispatches inline instead of detached")
    tk.set_defaults(fn=cmd_tick)

    ag = sub.add_parser("agent", help="the roster").add_subparsers(dest="agent_cmd", required=True)
    aa = ag.add_parser("add")
    aa.add_argument("--role", required=True, choices=["planner", "executor", "verifier"])
    aa.add_argument("--driver", required=True)
    aa.add_argument("--id")
    aa.add_argument("--max-concurrency", type=int, default=1)
    aa.add_argument("--task-classes")
    al = ag.add_parser("list")
    al.add_argument("--role")
    for name in ("pause", "resume", "retire"):
        x = ag.add_parser(name)
        x.add_argument("agent_id")
        x.add_argument("--reason")
        x.set_defaults(fn=cmd_agent)
    aa.set_defaults(fn=cmd_agent)
    al.set_defaults(fn=cmd_agent)

    sc = sub.add_parser("schedule", help="when agents wake").add_subparsers(dest="schedule_cmd", required=True)
    sa = sc.add_parser("add")
    sa.add_argument("--kind", required=True, choices=["planner", "executor", "verifier", "recurring"])
    sa.add_argument("--cron", required=True, help="5-field cron, UTC")
    sa.add_argument("--agent")
    sa.add_argument("--plan", help="approved recurring plan (kind=recurring)")
    sa.set_defaults(fn=cmd_schedule)
    sc.add_parser("list").set_defaults(fn=cmd_schedule)
    for name in ("enable", "disable"):
        x = sc.add_parser(name)
        x.add_argument("schedule_id")
        x.set_defaults(fn=cmd_schedule)

    me = sub.add_parser("memory", help="project memory").add_subparsers(dest="memory_cmd", required=True)
    ma = me.add_parser("add")
    ma.add_argument("--scope", required=True, help="project ref, class:<task_class>, global, agent:<id>")
    ma.add_argument("--kind", required=True)
    ma.add_argument("--content", required=True)
    ma.add_argument("--provenance")
    ma.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    ma.add_argument("--pin", action="store_true")
    ms = me.add_parser("search")
    ms.add_argument("query")
    ms.add_argument("--scope")
    ms.add_argument("--limit", type=int, default=10)
    for name in ("pin", "unpin", "retire"):
        x = me.add_parser(name)
        x.add_argument("item_id")
        x.set_defaults(fn=cmd_memory)
    st = me.add_parser("state")
    st.add_argument("scope")
    st.add_argument("--summary")
    st.add_argument("--focus")
    st.add_argument("--next")
    cx = me.add_parser("context", help="preview the context bundle an agent would receive")
    cx.add_argument("task_id")
    cx.add_argument("--role", default="plan", choices=["plan", "execute", "verify"])
    cx.add_argument("--budget", type=int)
    for x in (ma, ms, st, cx):
        x.set_defaults(fn=cmd_memory)

    ev = sub.add_parser("events", help="audit trail")
    ev.add_argument("--id")
    ev.add_argument("--limit", type=int, default=30)
    ev.set_defaults(fn=cmd_events)
    sub.add_parser("drivers", help="list available harness drivers").set_defaults(fn=cmd_driver)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        rc = args.fn(args)
        return rc or 0
    except HomeNotSet as e:
        print(f"holon: {e}", file=sys.stderr)
        return 2
    except (LookupError, ValueError, RuntimeError) as e:
        if args.json:
            print(json.dumps({"error": type(e).__name__, "message": str(e)}))
        else:
            print(f"holon: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
