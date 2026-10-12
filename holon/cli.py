"""`holon`: the single entry point to the organization. Human-readable by default, `--json` for agents.

You (the Chair) act by default. Employees act with `--as <emp_id>`.
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
GRADE = {0: "G0 routine", 1: "G1 minor", 2: "G2 significant", 3: "G3 major", 4: "G4 critical"}


def _out(args, data, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        print(text if text is not None else json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _task_line(t: dict) -> str:
    return f"{t['id']}  [{t['status']}] p{t['priority']}  {t['title']}" + (
        f"  — {t['blocked_reason']}" if t.get("blocked_reason") else "")


def _text_arg(v: str | None) -> str | None:
    """'-' reads stdin; '@path' reads a file; anything else is literal."""
    if v == "-":
        return sys.stdin.read()
    if v and v.startswith("@"):
        return Path(v[1:]).expanduser().read_text()
    return v


def _kernel(args):
    from holon.execution.service import Kernel
    return Kernel(Home.resolve(args.home), actor=args.actor)


# ── organization ──────────────────────────────────────────────────────────
def cmd_init(args):
    k = _kernel(args)
    if not k.home.config_file.exists():
        k.home.config_file.write_text(
            "# holon instance config\n[context]\nbudget_chars = 24000\n\n[planner]\nenforce_read_only = true\n\n"
            "[grading]\nnovelty_min_verified = 3\nsensitive_paths = []\n\n"
            "[autonomy]\n# supervised (safe default) | delegated | autonomous; keys below override the preset\n"
            "mode = \"supervised\"\n# verify_sample_rate = 0.2   # share of trusted G0 results still verified\n"
            "# widen_approver = \"chair\" # who approves widening authority: chair | ceo | auto\n"
            "# [autonomy.dept.<slug>]     # per department, once it has earned trust\n# mode = \"delegated\"\n\n"
            "[memory]\npromotion_threshold = 2\n")
    _out(args, {"home": str(k.home.root)}, f"holon initialized at {k.home.root} (executive department ready; "
                                           f"hire a CEO with: holon hr hire --dept executive --head ...)")


def cmd_status(args):
    k = _kernel(args)
    counts = k.work.counts()
    mine = k.work.awaiting_approval("chair")
    delegated = [p for p in k.work.awaiting_approval(None) if p["approver"] != "chair"]
    needs = k.work.list_tasks(status="needs_input")
    samples = k.audit_samples()
    data = {"tasks": counts, "awaiting_chair": len(mine), "awaiting_delegated": len(delegated),
            "needs_input": len(needs), "audit_samples": len(samples),
            "employees": len(k.org.employees()), "departments": len(k.org.depts())}
    _out(args, data, "\n".join([
        "tasks: " + (", ".join(f"{s}={n}" for s, n in sorted(counts.items())) or "none"),
        f"awaiting YOUR approval: {len(mine)}   awaiting CEO/HOD approval: {len(delegated)}",
        f"needs your input: {len(needs)}   audit samples to judge: {len(samples)}",
        f"organization: {data['departments']} departments, {data['employees']} employees"]))


def cmd_org(args):
    k = _kernel(args)
    out, lines = [], []
    for d in k.org.depts():
        emps = k.org.employees(d["slug"])
        out.append({**d, "employees": emps})
        lines.append(f"{d['slug']}  ({d['name']})" + (f" — {d['mission']}" if d["mission"] else ""))
        for e in sorted(emps, key=lambda e: not e["is_head"]):
            lines.append(f"  {'HOD ' if e['is_head'] else '    '}{e['id']}  {e['name']}, {e['title']}  "
                         f"[{e['status']}] roles={','.join(e['roles'])} via {e['driver']}")
    _out(args, out, "\n".join(lines) or "no departments")


def cmd_dept(args):
    k = _kernel(args)
    from holon.execution import genome
    if args.dept_cmd == "create":
        d = k.org.create_dept(args.slug, args.name, _text_arg(args.mission), args.parent,
                              genome.load_profile(args.profile, k.home))
        st = k.org.staffing(d["slug"])
        _out(args, d, f"{d['id']}  {d['slug']} created with profile {d['profile']}; slots to fill: "
                      + ", ".join(f"{s['slot']} ({'+'.join(s['roles'])})" for s in st["slots"]))
    elif args.dept_cmd == "list":
        ds = k.org.depts()
        _out(args, ds, "\n".join(f"{d['slug']:<20} {d.get('profile') or '-':<10} head={d['head_name'] or '-'}  {d['name']}"
                                 for d in ds))
    elif args.dept_cmd == "roles":
        st = k.org.staffing(args.slug)
        lines = [f"{st['dept']}: profile {st['profile']} — {'complete' if st['complete'] else 'INCOMPLETE'}"]
        for s in st["slots"]:
            lines.append(f"  {'ok ' if s['ok'] else '-- '} {s['slot']:<12} {'+'.join(s['roles']):<22} "
                         + (", ".join(s["filled_by"]) or f"vacant (needs {s['min']})"))
        lines += [f"  ! {w}" for w in st["warnings"]]
        _out(args, st, "\n".join(lines))
    elif args.dept_cmd == "profile":
        if k.actor != "chair":
            raise PermissionError("only the Chair changes a department's genome profile")
        d = k.org.set_profile(args.slug, genome.load_profile(args.profile, k.home), args.reason)
        _out(args, d, f"{d['slug']} now follows profile {d['profile']}")
    elif args.dept_cmd == "profiles":
        out = []
        for name in sorted(genome.profile_paths(k.home)):
            g = genome.load_profile(name, k.home)
            out.append(g)
        _out(args, out, "\n".join(f"{g['name']:<12} {g['description']}\n" + "\n".join(
            f"    {s['name']:<12} {'+'.join(s['roles'])}" for s in g["slots"]) for g in out))


def cmd_hr(args):
    k = _kernel(args)
    o = k.org
    if args.hr_cmd == "hire":
        e = o.hire(name=args.name, title=args.title, dept=args.dept, job_description=_text_arg(args.jd) or "",
                   duties=args.duties.split(",") if args.duties else None,
                   roles=args.roles.split(",") if args.roles else None, slot=args.slot,
                   driver=args.driver, reason=args.reason, head=args.head,
                   reports_to=args.reports_to, task_classes=args.task_classes.split(",") if args.task_classes else None,
                   skills=args.skills.split(",") if args.skills else None, max_concurrency=args.max_concurrency,
                   status=args.status)
        _out(args, e, f"hired {e['id']}  {e['name']}, {e['title']} in {e['dept']}" + (" (HOD)" if e["is_head"] else ""))
    elif args.hr_cmd == "fire":
        e = o.fire(args.employee, args.reason, args.successor)
        _out(args, e, f"{e['id']} retired" + (f"; memory passes to {args.successor}" if args.successor else ""))
    elif args.hr_cmd in ("pause", "activate"):
        e = o.set_status(args.employee, "paused" if args.hr_cmd == "pause" else "active", args.reason)
        _out(args, e, f"{e['id']} -> {e['status']}")
    elif args.hr_cmd == "review":
        _out(args, o.review(args.employee))


def cmd_emp(args):
    k = _kernel(args)
    if args.emp_cmd == "list":
        es = k.org.employees(args.dept, include_retired=args.all)
        _out(args, es, "\n".join(f"{e['id']}  {e['dept']:<14} {e['name']}, {e['title']} [{e['status']}]" for e in es)
             or "no employees")
    else:
        e = k.org.employee(args.employee)
        _out(args, e, f"{e['name']}, {e['title']} ({e['dept']}{', HOD' if e['is_head'] else ''}) [{e['status']}]\n"
                      f"roles: {', '.join(e['roles'])} · duties: {', '.join(e['duties'])} · driver: {e['driver']}"
                      f" · reports to: {e['reports_to'] or 'Chair'}"
                      f"\n\n{e['job_description']}")


# ── work ──────────────────────────────────────────────────────────────────
def cmd_task(args):
    k = _kernel(args)
    w = k.work
    if args.task_cmd == "add":
        t = w.add_task(args.title, _text_arg(args.intent), acceptance=_text_arg(args.acceptance),
                       project_ref=args.project, parent_id=args.parent, dept=args.dept, assignee=args.assign,
                       priority=args.priority, source="chair", task_class=args.task_class, risk=args.risk,
                       workdir=str(Path(args.workdir).expanduser().resolve()) if args.workdir else None,
                       due=args.due, max_attempts=args.max_attempts)
        _out(args, t, _task_line(t))
    elif args.task_cmd == "list":
        dept_id = k.org.dept(args.dept)["id"] if args.dept else None
        ts = w.list_tasks(status=args.status, project_ref=args.project, dept_id=dept_id, assignee=args.assignee,
                          limit=args.limit)
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
            lines.append(f"\nplan v{p['version']} {p['id']} [{p['status']}] {GRADE[p['grade']]} → {p['approver']}"
                         + (f" — decided by {p['decided_by']}: {p['review_note']}" if p["decided_by"] else ""))
        if data["result"]:
            r = data["result"]
            lines += ["", f"result [{r['outcome']}] verified={r['verified']}: {r['summary']}", "", r["documentation"]]
        for c in data["children"]:
            lines.append("  sub-task " + _task_line(c))
        _out(args, data, "\n".join(lines))
    elif args.task_cmd == "cancel":
        _out(args, w.cancel(args.task_id, args.note), f"cancelled {args.task_id}")
    elif args.task_cmd == "retry":
        _out(args, w.retry(args.task_id), f"{args.task_id} -> approved (retry)")
    elif args.task_cmd == "replan":
        _out(args, w.replan(args.task_id, args.note or "chair replan"), f"{args.task_id} -> inbox (replan)")
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
    parts = [f"{p['id']}  v{p['version']} [{p['status']}] {p['kind']}  {GRADE[p['grade']]} → {p['approver']}"
             f"  task={p['task_id']}  author={p['author'] or '-'}",
             "why this grade: " + "; ".join(json.loads(p["grade_reasons"] or "[]")),
             f"\nfindings:\n  {p['findings']}", f"\nsteps:\n{steps}"]
    for key in ("writes", "external_effects", "assignee", "risks", "rollback", "estimate"):
        if p.get(key):
            parts.append(f"\n{key}: {p[key]}")
    return "\n".join(parts)


def cmd_plan(args):
    k = _kernel(args)
    if args.plan_cmd == "queue":
        approver = None if args.approver == "all" else args.approver
        q = k.work.awaiting_approval(approver)
        _out(args, q, "\n".join(f"{p['id']}  G{p['grade']} p{p['priority']} → {p['approver']:<10} v{p['version']} "
                                f"{p['kind']:<9} {p['task_title']}" for p in q) or "nothing awaiting approval")
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
    elif args.plan_cmd == "grade":
        g = int(args.grade.upper().lstrip("G"))
        p = k.regrade(args.plan_id, g, args.note)
        _out(args, p, f"{p['id']} regraded to {GRADE[p['grade']]} → {p['approver']}")


def cmd_claim(args):
    k = _kernel(args)
    c = k.claim(args.duty, args.as_, args.driver, args.task)
    if c is None:
        _out(args, {"claimed": None}, f"nothing to claim for duty '{args.duty}'")
        return NOTHING
    data = {"run_id": c.run["id"], "task_id": c.task["id"], "brief_dir": str(c.brief_dir),
            "brief_md": str(c.brief_dir / "BRIEF.md"), "result_path": str(c.brief_dir / "result.json"),
            "lease_until": c.run["lease_until"]}
    _out(args, data, f"claimed {c.task['id']} ({args.duty}): run {c.run['id']}\n"
                     f"read  {data['brief_md']}\nwrite {data['result_path']}\nthen  holon submit {c.run['id']}")


def cmd_submit(args):
    k = _kernel(args)
    env = json.loads(Path(args.file).read_text()) if args.file else None
    out = k.submit(args.run_id, env)
    t = out.get("task", {})
    msg = f"run {args.run_id} ingested; task {t.get('id')} -> {t.get('status')}"
    if out.get("plan"):
        msg += f"\nplan {out['plan']['id']} {GRADE[out['plan']['grade']]} → {out['plan']['approver']}"
    if out.get("breach"):
        msg += "\ncontract breach: " + "; ".join(out["breach"])
    for p in (out.get("memory") or {}).get("promotions", []):
        msg += f"\nlesson promoted to {p['to']}: {p['item']}"
    _out(args, out, msg)


def cmd_run(args):
    k = _kernel(args)
    if args.run_cmd == "heartbeat":
        r = k.work.heartbeat(args.run_id)
        _out(args, r, f"lease extended to {r['lease_until']}")
    else:
        _out(args, {"run": k.work.get_run(args.run_id), "result": k.work.result_for_run(args.run_id)})


def cmd_verify(args):
    k = _kernel(args)
    out = k.verify(args.task_id, args.verdict, args.note)
    _out(args, out, f"{args.task_id} -> {out['task']['status']}")


def cmd_dispatch(args):
    k = _kernel(args)
    try:
        out = k.dispatch(args.duty, args.as_, args.task)
    except Exception:
        if args.schedule:
            k.schedules.record_outcome(args.schedule, False)
        raise
    if args.schedule:
        k.schedules.record_outcome(args.schedule, True)
    if out is None:
        _out(args, {"dispatched": None}, f"nothing to do for {args.as_} ({args.duty})")
        return NOTHING
    t = out.get("task", {})
    _out(args, out, f"{args.duty} run done; task {t.get('id')} -> {t.get('status')}")


def cmd_tick(args):
    k = _kernel(args)
    out = k.tick(foreground=args.foreground)
    _out(args, out, f"swept {len(out['swept'])} expired leases; fired {len(out['fired'])} schedules")


def cmd_schedule(args):
    k = _kernel(args)
    s = k.schedules
    if args.schedule_cmd == "add":
        row = s.add(args.kind, args.cron, employee_id=args.as_, plan_id=args.plan)
        _out(args, row, f"{row['id']}  {row['kind']} '{row['cron']}' next {row['next_run_at']}")
    elif args.schedule_cmd == "list":
        rows = s.list()
        _out(args, rows, "\n".join(f"{r['id']}  {r['kind']:<9} '{r['cron']}' {'on ' if r['enabled'] else 'off'} "
                                   f"next {r['next_run_at']} {r['employee_id'] or r['plan_id']}" for r in rows)
             or "no schedules")
    else:
        row = s.set_enabled(args.schedule_id, args.schedule_cmd == "enable")
        _out(args, row, f"{row['id']} {args.schedule_cmd}d")


# ── communication, memory, governance ─────────────────────────────────────
def cmd_msg(args):
    k = _kernel(args)
    sender = f"emp:{args.as_}" if args.as_ else "chair"
    if args.msg_cmd == "send":
        mid = k.work.post_message(sender, args.to, _text_arg(args.body), kind="channel" if args.to.startswith(
            ("dept:", "org")) else "dm", task_id=args.task)
        _out(args, {"id": mid}, f"sent {mid} to {args.to}")
    elif args.msg_cmd == "inbox":
        ms = k.inbox(sender)
        _out(args, ms, "\n".join(f"{m['ts'][:16]} [{m['kind']}] {m['sender']} → {m['to_ref']}: {m['body'][:160]}"
                                 for m in ms) or "inbox empty")
    elif args.msg_cmd == "request":
        t = k.request(sender, args.dept, args.title, _text_arg(args.intent), acceptance=_text_arg(args.acceptance),
                      priority=args.priority)
        _out(args, t, f"request filed as {t['id']} in {args.dept}")


def cmd_memory(args):
    k = _kernel(args)
    m = k.memory
    if args.memory_cmd == "add":
        it = m.add_item(args.scope, args.kind, _text_arg(args.content), args.provenance or args.actor,
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
    elif args.memory_cmd == "promote":
        it = m.get_item(args.item_id)
        up = m.add_item(args.to, it["kind"], it["content"], f"promoted:{it['id']}", confidence="high")
        _out(args, up, f"{up['id']} {up['action']} in {args.to}")
    elif args.memory_cmd == "state":
        if any(v is not None for v in (args.summary, args.focus, args.next)):
            st = m.update_state(args.scope, summary=args.summary, current_focus=args.focus, next_steps=args.next)
        else:
            st = m.get_state(args.scope)
        _out(args, st)
    elif args.memory_cmd == "context":
        from holon.capabilities.context import compile_context
        emp = k.org.employee(args.as_) if args.as_ else None
        md, meta = compile_context(k.home, k.work, m, args.task_id, args.duty,
                                   budget=args.budget or int(k.config["context"]["budget_chars"]), employee=emp,
                                   predecessors=k.org.predecessors(emp["id"]) if emp else None)
        _out(args, {"markdown": md, **meta}, md)


def cmd_charter(args):
    k = _kernel(args)
    if args.charter_cmd == "authority":
        if args.set_for:
            r = k.set_authority(args.set_for, args.task_class, int(args.max_grade.upper().lstrip("G")), args.reason)
            if not r["applied"]:
                print(f"widening needs approval: proposal {r['proposal']} -> {r['approver']}", file=sys.stderr)
        rows = k.org.authority_table()
        _out(args, rows, "\n".join(f"{r['approver']:<34} {r['task_class']:<16} up to G{r['max_grade']}  ({r['reason']})"
                                   for r in rows))


    elif args.charter_cmd == "autonomy":
        rows = [{"dept": d["slug"], **k.org.policy(d["slug"])} for d in k.org.depts()]
        org = {"dept": "(organization)", **k.org.policy()}
        _out(args, [org] + rows, "\n".join(
            f"{r['dept']:<16} {r['mode']:<11} widen→{r['widen_approver']:<6} earned≤G{r['auto_promote_cap']} "
            f"after {r['promote_after']}  verify trusted G0: {r['verify_sample_rate']:.0%} (trusted after "
            f"{r['trusted_after']})  audit {r['audit_sample_permille'] / 10:.0f}%" for r in [org] + rows))


def cmd_skill(args):
    k = _kernel(args)
    lib = k.skills
    dept = args.dept or (k.org.employee(args.as_)["dept"] if getattr(args, "as_", None) else None)
    c = args.skill_cmd
    if c == "list":
        rows = lib.catalog(dept, args.role)
        if getattr(args, "as_", None):
            mine = {"holon-employee", "holon-collaboration", "holon-memory"} | {
                f"holon-{r}" for r in k.org.employee(args.as_)["roles"]} | set(k.org.employee(args.as_)["skills"] or [])
            rows = [m for m in rows if m["name"] in mine]
        _out(args, rows, "\n".join(f"{m['name']:<24} {m.get('kind', '?'):<10} {m['source']:<14} "
                                   f"{','.join(m.get('roles') or [])}\n    {m.get('description', '')[:140]}" for m in rows)
             or "no skills")
    elif c == "show":
        layer, path = lib.resolve(args.name, args.file, dept)
        _out(args, {"layer": layer, "path": str(path), "text": path.read_text()}, path.read_text())
    elif c == "path":
        layer, path = lib.resolve(args.name, args.file, dept)
        _out(args, {"layer": layer, "path": str(path)}, str(path))
    elif c == "invoke":
        r = k.invoke_skill(args.name, args.file, getattr(args, "as_", None), args.dept)
        _out(args, r, r["text"])
    elif c == "fork":
        written = lib.fork(args.name, args.dept, args.file)
        _out(args, [str(p) for p in written], "\n".join(f"forked {p}" for p in written) or "nothing to fork (already tailored)")
    elif c == "new":
        p = lib.new(args.name, args.kind, args.description, args.roles.split(",") if args.roles else None, args.dept)
        _out(args, {"path": str(p)}, f"created {p}")
    elif c == "check":
        from holon.execution.genome import ROLES
        problems = lib.check(args.dept, list(ROLES))
        _out(args, {"problems": problems}, "\n".join(problems) or f"{len(lib.names(args.dept))} skills ok")
        return 1 if problems else 0
    elif c == "export":
        from holon.execution.briefs import holon_command
        out = lib.export(Path(args.to).expanduser(), {"holon": holon_command(k.home)}, args.name or None, dept)
        _out(args, [str(p) for p in out], "\n".join(f"exported {p}" for p in out))


def cmd_audit(args):
    k = _kernel(args)
    if args.audit_cmd == "samples":
        rows = k.audit_samples(pending_only=not args.all)
        _out(args, rows, "\n".join(f"{r['plan_id']}  G{r['grade']} approved by {r['approver']}: {r['title']}"
                                   + (f"  [{r['verdict']}]" if r["verdict"] else "") for r in rows)
             or "no audit samples pending")
    elif args.audit_cmd == "judge":
        out = k.judge_sample(args.plan_id, args.verdict, args.note)
        _out(args, out, f"{args.plan_id}: {args.verdict}" + (
            f"; authority {out['authority_change']['from']}→{out['authority_change']['to']}"
            if out["authority_change"] else ""))


def cmd_events(args):
    k = _kernel(args)
    ev = k.work.events(args.id, args.limit)
    _out(args, ev, "\n".join(f"{e['ts'][:19]} {e['entity']} {e['entity_id']} {e['from_status'] or '-'} -> "
                             f"{e['to_status'] or '-'} by {e['actor']}" + (f": {e['note']}" if e["note"] else "")
                             for e in ev))


def cmd_drivers(args):
    from holon.execution import drivers
    ds = drivers.available(Home.resolve(args.home))
    _out(args, {n: d.source for n, d in ds.items()}, "\n".join(f"{n:<16} {d.source}" for n, d in ds.items()))


# ── parser ────────────────────────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="holon", description="Holon: an agentic OS that runs as an organization")
    p.add_argument("--home", help="instance root (default: $HOLON_HOME)")
    p.add_argument("--actor", default=os.environ.get("HOLON_ACTOR", "chair"), help="audit-trail actor (default: chair)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--version", action="version", version=f"holon {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    def cmd(name, fn, help_=None, parent=sub):
        sp = parent.add_parser(name, help=help_)
        sp.set_defaults(fn=fn)
        return sp

    def as_(sp, required=False):
        sp.add_argument("--as", dest="as_", required=required, metavar="EMP_ID", help="act as this employee")
        return sp

    cmd("init", cmd_init, "create the instance and the executive department")
    cmd("status", cmd_status, "what needs you, and how the organization is doing")
    cmd("org", cmd_org, "the org chart")

    d = cmd("dept", cmd_dept, "departments").add_subparsers(dest="dept_cmd", required=True)
    dc = cmd("create", cmd_dept, parent=d)
    dc.add_argument("slug")
    dc.add_argument("--name", required=True)
    dc.add_argument("--mission", help="text, '-' for stdin, or @file")
    dc.add_argument("--parent", help="parent department slug (default: executive)")
    dc.add_argument("--profile", default="light", help="genome profile: light | standard | <instance profile> | file.toml")
    cmd("list", cmd_dept, parent=d)
    cmd("roles", cmd_dept, "the department's role slots and who fills them", parent=d).add_argument("slug")
    dp = cmd("profile", cmd_dept, "change a department's genome profile (Chair)", parent=d)
    dp.add_argument("slug")
    dp.add_argument("profile")
    dp.add_argument("--reason", required=True)
    cmd("profiles", cmd_dept, "available genome profiles", parent=d)

    h = cmd("hr", cmd_hr, "hire, fire, review").add_subparsers(dest="hr_cmd", required=True)
    hi = cmd("hire", cmd_hr, parent=h)
    hi.add_argument("--name", required=True)
    hi.add_argument("--title", required=True)
    hi.add_argument("--dept", required=True)
    hi.add_argument("--jd", required=True, help="job description: text, '-' for stdin, or @file")
    hi.add_argument("--slot", help="fill this slot of the department's profile (gives its roles)")
    hi.add_argument("--roles", help="comma list of head,researcher,planner,executor,verifier,steward")
    hi.add_argument("--duties", help="legacy: comma list of plan,execute,verify,review (derived from roles)")
    hi.add_argument("--driver", required=True, help="harness driver name (see `holon drivers`)")
    hi.add_argument("--reason", required=True)
    hi.add_argument("--head", action="store_true", help="make this employee the department's head")
    hi.add_argument("--reports-to")
    hi.add_argument("--task-classes")
    hi.add_argument("--skills")
    hi.add_argument("--max-concurrency", type=int, default=1)
    hi.add_argument("--status", default="probation", choices=["probation", "active"])
    fi = cmd("fire", cmd_hr, parent=h)
    fi.add_argument("employee")
    fi.add_argument("--reason", required=True)
    fi.add_argument("--successor", help="employee who inherits the role and memory")
    for name in ("pause", "activate"):
        x = cmd(name, cmd_hr, parent=h)
        x.add_argument("employee")
        x.add_argument("--reason", required=True)
    cmd("review", cmd_hr, parent=h).add_argument("employee")

    e = cmd("emp", cmd_emp, "employees").add_subparsers(dest="emp_cmd", required=True)
    el = cmd("list", cmd_emp, parent=e)
    el.add_argument("--dept")
    el.add_argument("--all", action="store_true", help="include retired")
    cmd("show", cmd_emp, parent=e).add_argument("employee")

    t = cmd("task", cmd_task, "intake and Chair controls").add_subparsers(dest="task_cmd", required=True)
    ta = cmd("add", cmd_task, parent=t)
    ta.add_argument("title")
    ta.add_argument("--intent", required=True, help="the outcome wanted: text, '-' for stdin, or @file")
    ta.add_argument("--acceptance")
    ta.add_argument("--project", help="project ref, e.g. traj:<node-id>")
    ta.add_argument("--parent")
    ta.add_argument("--dept", help="department slug (else the CEO triages it)")
    ta.add_argument("--assign", help="employee id")
    ta.add_argument("--priority", type=int, default=3)
    ta.add_argument("--task-class")
    ta.add_argument("--risk", default="normal", choices=["low", "normal", "high", "irreversible"])
    ta.add_argument("--workdir")
    ta.add_argument("--due")
    ta.add_argument("--max-attempts", type=int, default=2)
    tl = cmd("list", cmd_task, parent=t)
    tl.add_argument("--status", help="comma-separated")
    tl.add_argument("--project")
    tl.add_argument("--dept")
    tl.add_argument("--assignee")
    tl.add_argument("--limit", type=int, default=50)
    cmd("show", cmd_task, parent=t).add_argument("task_id")
    tc = cmd("cancel", cmd_task, parent=t)
    tc.add_argument("task_id")
    tc.add_argument("--note", required=True)
    cmd("retry", cmd_task, parent=t).add_argument("task_id")
    tr = cmd("replan", cmd_task, parent=t)
    tr.add_argument("task_id")
    tr.add_argument("--note")
    tn = cmd("answer", cmd_task, "answer a planner's question", parent=t)
    tn.add_argument("task_id")
    tn.add_argument("text")

    pl = cmd("plan", cmd_plan, "approvals").add_subparsers(dest="plan_cmd", required=True)
    pq = cmd("queue", cmd_plan, parent=pl)
    pq.add_argument("--approver", default="chair", help="chair (default), emp:<id>, or all")
    cmd("show", cmd_plan, parent=pl).add_argument("plan_id")
    pa = cmd("approve", cmd_plan, parent=pl)
    pa.add_argument("plan_id")
    pa.add_argument("--note")
    pa.add_argument("--recurring", action="store_true", help="approve as a template for a recurring schedule")
    pr = cmd("reject", cmd_plan, parent=pl)
    pr.add_argument("plan_id")
    pr.add_argument("--note", required=True, help="what the planner must change (it learns from this)")
    pg = cmd("grade", cmd_plan, "Chair regrade (re-routes approval)", parent=pl)
    pg.add_argument("plan_id")
    pg.add_argument("grade", help="G0..G4")
    pg.add_argument("--note", required=True)

    cl = as_(cmd("claim", cmd_claim, "pull mode: take one unit of work"))
    cl.add_argument("--duty", required=True, choices=["plan", "execute", "verify", "review"])
    cl.add_argument("--driver")
    cl.add_argument("--task")
    sm = cmd("submit", cmd_submit, "pull mode: hand back result.json for a claimed run")
    sm.add_argument("run_id")
    sm.add_argument("--file", help="result file (default: the run's result.json)")
    rn = cmd("run", cmd_run, "run leases").add_subparsers(dest="run_cmd", required=True)
    cmd("heartbeat", cmd_run, parent=rn).add_argument("run_id")
    cmd("show", cmd_run, parent=rn).add_argument("run_id")
    v = cmd("verify", cmd_verify, "Chair verification of a result")
    v.add_argument("task_id")
    v.add_argument("verdict", choices=["pass", "fail"])
    v.add_argument("--note", required=True)
    dp = as_(cmd("dispatch", cmd_dispatch, "push mode: claim one unit for an employee and run its driver"), True)
    dp.add_argument("--duty", required=True, choices=["plan", "execute", "verify", "review"])
    dp.add_argument("--task")
    dp.add_argument("--schedule", help=argparse.SUPPRESS)
    cmd("tick", cmd_tick, "self-healing sweep + fire due schedules (call from any timer)").add_argument(
        "--foreground", action="store_true")

    sc = cmd("schedule", cmd_schedule, "when employees wake").add_subparsers(dest="schedule_cmd", required=True)
    sa = as_(cmd("add", cmd_schedule, parent=sc))
    sa.add_argument("--kind", required=True, choices=["plan", "execute", "verify", "review", "recurring"])
    sa.add_argument("--cron", required=True, help="5-field cron, UTC")
    sa.add_argument("--plan", help="approved recurring plan (kind=recurring)")
    cmd("list", cmd_schedule, parent=sc)
    for name in ("enable", "disable"):
        cmd(name, cmd_schedule, parent=sc).add_argument("schedule_id")

    mg = cmd("msg", cmd_msg, "talk to colleagues and departments").add_subparsers(dest="msg_cmd", required=True)
    ms = as_(cmd("send", cmd_msg, parent=mg))
    ms.add_argument("to", help="emp:<id> | dept:<slug> | org | chair")
    ms.add_argument("body")
    ms.add_argument("--task")
    as_(cmd("inbox", cmd_msg, parent=mg))
    mr = as_(cmd("request", cmd_msg, "ask another department for work (creates a task there)", parent=mg))
    mr.add_argument("dept")
    mr.add_argument("title")
    mr.add_argument("--intent", required=True)
    mr.add_argument("--acceptance")
    mr.add_argument("--priority", type=int, default=3)

    me = cmd("memory", cmd_memory, "institutional memory").add_subparsers(dest="memory_cmd", required=True)
    ma = cmd("add", cmd_memory, parent=me)
    ma.add_argument("--scope", required=True, help="emp:<id> | dept:<slug> | org | <project ref> | class:<name>")
    ma.add_argument("--kind", required=True)
    ma.add_argument("--content", required=True)
    ma.add_argument("--provenance")
    ma.add_argument("--confidence", default="medium", choices=["low", "medium", "high"])
    ma.add_argument("--pin", action="store_true")
    mq = cmd("search", cmd_memory, parent=me)
    mq.add_argument("query")
    mq.add_argument("--scope")
    mq.add_argument("--limit", type=int, default=10)
    for name in ("pin", "unpin", "retire"):
        cmd(name, cmd_memory, parent=me).add_argument("item_id")
    mp = cmd("promote", cmd_memory, parent=me)
    mp.add_argument("item_id")
    mp.add_argument("--to", required=True, help="dept:<slug> or org")
    mst = cmd("state", cmd_memory, parent=me)
    mst.add_argument("scope")
    mst.add_argument("--summary")
    mst.add_argument("--focus")
    mst.add_argument("--next")
    mc = as_(cmd("context", cmd_memory, "preview the brief context an employee would receive", parent=me))
    mc.add_argument("task_id")
    mc.add_argument("--duty", default="plan", choices=["plan", "execute", "verify", "review"])
    mc.add_argument("--budget", type=int)

    ch = cmd("charter", cmd_charter, "the organization's rules").add_subparsers(dest="charter_cmd", required=True)
    au = cmd("authority", cmd_charter, "who may approve which grade", parent=ch)
    au.add_argument("--set-for", help="approver to set: chair or emp:<id>")
    au.add_argument("--task-class", default="*")
    au.add_argument("--max-grade", default="G1")
    au.add_argument("--reason", default="set by the Chair")

    cmd("autonomy", cmd_charter, "effective autonomy policy per department (holon.toml [autonomy])", parent=ch)

    sk = cmd("skill", cmd_skill, "the skills library: list, read, invoke, tailor, check, export").add_subparsers(
        dest="skill_cmd", required=True)

    def skill_cmd(name, help_):
        sp = cmd(name, cmd_skill, help_, parent=sk)
        sp.add_argument("--dept", help="resolve with this department's tailored layer")
        return sp
    sl = as_(skill_cmd("list", "skills available (to an employee with --as)"))
    sl.add_argument("--role")
    for name, help_ in (("show", "print a skill file"), ("path", "where a skill file resolves"),
                        ("invoke", "render a skill for an employee and record the use")):
        x = skill_cmd(name, help_)
        x.add_argument("name")
        x.add_argument("file", nargs="?", default="SKILL.md")
        if name == "invoke":
            as_(x)
    sf = skill_cmd("fork", "copy a skill (or one file) into the instance or --dept layer to tailor it")
    sf.add_argument("name")
    sf.add_argument("--file")
    sn = skill_cmd("new", "scaffold a new skill in the instance (or --dept) layer")
    sn.add_argument("name")
    sn.add_argument("--kind", required=True, choices=["protocol", "role", "operation"])
    sn.add_argument("--description", required=True)
    sn.add_argument("--roles")
    skill_cmd("check", "validate every skill (frontmatter, links, roles)")
    se = skill_cmd("export", "write resolved, rendered skills to a directory a harness loads skills from")
    se.add_argument("--to", required=True)
    se.add_argument("--name", action="append")

    ad = cmd("audit", cmd_audit, "holdout review of delegated approvals").add_subparsers(dest="audit_cmd", required=True)
    cmd("samples", cmd_audit, parent=ad).add_argument("--all", action="store_true")
    aj = cmd("judge", cmd_audit, parent=ad)
    aj.add_argument("plan_id")
    aj.add_argument("verdict", choices=["agree", "disagree"])
    aj.add_argument("--note", required=True)

    ev = cmd("events", cmd_events, "audit trail")
    ev.add_argument("--id")
    ev.add_argument("--limit", type=int, default=30)
    cmd("drivers", cmd_drivers, "available harness drivers")
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.fn(args) or 0
    except HomeNotSet as e:
        print(f"holon: {e}", file=sys.stderr)
        return 2
    except (LookupError, ValueError, RuntimeError, OSError, PermissionError) as e:
        if args.json:
            print(json.dumps({"error": type(e).__name__, "message": str(e)}))
        else:
            print(f"holon: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
