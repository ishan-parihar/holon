"""G12: the department archive — export a department, import it elsewhere.

There was no way to move a department, so every venture department was onboarded by
hand: a wave was per-pool toil instead of one command. The archive makes a wave
configuration.

**What the archive carries**: the department's profile and slots; its employees'
records (never their keys); its scope roots; its smoke checks; its secrets bindings
as **paths**; its routines with their playbooks; its knowledge-base bindings.

**What it never carries**: a secret value, and an approval. The secrets are paths to
0600 key files, exactly as `holon backup` does — a department archive travels, so a
key inside it would travel too. And an imported routine lands **unapproved**: an
import is not an approval, and a routine that reached the schedule unapproved would be
the outside-reach import the kernel deliberately disables.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from holon.core.db import now

MANIFEST = "department.json"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def export_department(k, dept_id: str, dest: str) -> dict:
    """Write one department's archive: its shape, its people, its routines, its reach.

    `dest` is a directory; the archive is `dest/<slug>-<timestamp>/`.
    """
    dept = k.org.dept(dept_id)
    slug = dept["slug"]
    out = Path(dest).expanduser().resolve() / f"{slug}-{now()[:19].replace(':', '').replace('-', '')}"
    out.mkdir(parents=True)

    employees = []
    for e in k.org.employees(dept_id):
        employees.append({"name": e["name"], "title": e["title"], "roles": list(e["roles"] or ""),
                          "driver": e["driver"], "job_description": e["job_description"],
                          "reports_to": (k.org.employee(e["reports_to"])["name"]
                                         if e.get("reports_to") else None),
                          "status": e["status"], "reason": e.get("reason") or ""})
    # the employees' keys are NEVER carried: only their hashes live here, and a hash in
    # an archive that travels is a credential that travels.

    # the scope roots and the smoke checks, from the instance's own config
    scope = (k.config.get("scope") or {}).get("dept", {}).get(slug) or {}
    smoke = (k.config.get("smoke") or {}).get("dept", {}).get(slug) or {}
    # the secrets bindings: PATHS, never values
    secrets = (k.config.get("secrets") or {}).get("dept", {}).get(slug) or {}

    routines = []
    for r in k.routines():
        plan = k.work.get(r["plan"])
        work = k.work.get(plan["parent_id"])
        if work.get("dept_id") != dept_id:
            continue
        body = (k.work.output(plan["id"]) or {"body": {}})["body"]
        routines.append({"title": r["title"], "cron": r["cron"], "lane": r["lane"],
                         "command": body.get("command"), "rubric": body.get("rubric") or [],
                         "intent": work["intent"], "acceptance": work.get("acceptance"),
                         "workdir": work.get("workdir"), "playbook": body.get("playbook")})

    kb = []
    try:
        from holon.capabilities import kb as kbcap
        for slug_, entry in (kbcap._registry(k.home) or {}).items():
            if entry.get("dept") == slug:
                kb.append({"slug": slug_, "blueprint": entry.get("blueprint")})
    except Exception:
        pass  # the KB registry is optional; a department without one is still exportable

    manifest = {
        "dept": {"slug": slug, "name": dept["name"], "mission": dept.get("mission"),
                 "profile": dept.get("profile"), "bylaws": dept.get("bylaws")},
        "employees": employees,
        "routines": routines,
        "scope": scope,
        "smoke": smoke,
        "secrets": dict(secrets),      # paths, never values
        "kb": kb,
        "created_at": now(),
        "instance_id": k.auth.instance_id,
    }
    (out / MANIFEST).write_text(json.dumps(manifest, indent=2, default=str))
    files = {str(p.relative_to(out)): _sha(p) for p in sorted(out.rglob("*")) if p.is_file()}
    (out / "checksums.json").write_text(json.dumps({"files": files}, indent=2))
    return {"path": str(out), "dept": slug, "employees": len(employees), "routines": len(routines)}


def import_department(k, archive: str) -> dict:
    """Create a department from an archive.

    Additive: a department that already exists is refused — changing one is the
    Chair's, not the archive's. Every routine lands **unapproved**; the Chair approves
    each one, as with any other routine.
    """
    src = Path(archive).expanduser().resolve()
    manifest_path = src / MANIFEST
    if not manifest_path.exists():
        raise ValueError(f"{src} is not a department archive (no {MANIFEST})")
    manifest = json.loads(manifest_path.read_text())
    d = manifest["dept"]
    slug = d["slug"]
    if any(x["slug"] == slug for x in k.org.depts()):
        raise ValueError(f"department {slug} already exists; an import does not replace one")

    # the department itself, with its profile
    from holon.execution import bylaws
    profile = bylaws.load_profile(d.get("profile") or "light", k.home)
    k.org.create_dept(slug, d["name"], d.get("mission") or "", profile=profile)

    # its people, by role (the head first, so the others have a manager)
    employees = sorted(manifest.get("employees") or [],
                        key=lambda e: 0 if "head" in (e.get("roles") or []) else 1)
    hired = 0
    for e in employees:
        try:
            k.org.hire(name=e["name"], title=e.get("title") or "", dept=slug,
                       job_description=e.get("job_description") or "", driver=e.get("driver") or "fake",
                       reason=e.get("reason") or "imported", roles=list(e.get("roles") or []),
                       status=e.get("status") or "active")
            hired += 1
        except (LookupError, ValueError):
            continue  # a slot the profile has no room for is the Chair's to widen

    # its routines, each as a proposal for the Chair's approval
    made = 0
    for r in manifest.get("routines") or []:
        try:
            k.add_routine(r["title"], r.get("intent") or r["title"], dept=slug, cron=r.get("cron"),
                          command=r.get("command"), rubric=r.get("rubric") or None,
                          workdir=r.get("workdir"), strict=False, imported=True)
            made += 1
        except (ValueError, LookupError):
            continue

    # its reach and its checks, into the instance's config
    scope = manifest.get("scope") or {}
    smoke = manifest.get("smoke") or {}
    secrets = manifest.get("secrets") or {}
    if scope or smoke or secrets:
        text = k.home.config_file.read_text() if k.home.config_file.exists() else ""
        add = []
        if scope:
            add.append(f"\n[scope.dept.{slug}]\nroots = {json.dumps(scope.get('roots') or [])}\n"
                       f"commands = {json.dumps(scope.get('commands') or [])}\n"
                       f"act_commands = {json.dumps(scope.get('act_commands') or [])}")
        if smoke:
            add.append(f"\n[smoke.dept.{slug}]")
            for c in smoke.get("checks") or []:
                add.append(f"\n[[smoke.dept.{slug}.checks]]\nname = {json.dumps(c.get('name', ''))}\n"
                           f"cmd = {json.dumps(c.get('cmd', ''))}\n"
                           f"critical = {'true' if c.get('critical') else 'false'}\n"
                           f"reason = {json.dumps(c.get('reason', ''))}")
        if secrets:
            add.append(f"\n[secrets.dept.{slug}]")
            for var, path in secrets.items():
                add.append(f"\n{var} = {json.dumps(path)}")   # the path, never the value
        k.home.config_file.write_text(text.rstrip() + "\n" + "\n".join(add) + "\n")

    return {"dept": slug, "employees": hired, "routines": made,
            "kb": [x["slug"] for x in manifest.get("kb") or []],
            "approved": 0, "note": "every imported routine is unapproved: an import is not an approval"}


def verify_archive(archive: str) -> dict:
    """The archive's checksums, so a reader can tell whether it is intact."""
    src = Path(archive).expanduser().resolve()
    sums = json.loads((src / "checksums.json").read_text())["files"]
    bad = [f for f, h in sums.items() if not (src / f).exists() or _sha(src / f) != h]
    return {"path": str(src), "files": len(sums), "corrupt": bad}
