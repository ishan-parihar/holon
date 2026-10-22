"""The bylaws layer (U1–U3): units and their services, declared not registered.

A **unit** is a thing with an identity the organization works through — a service its
departments consume or pool (a foundation in the legacy organism's terms), or a department's
own declared bylaws. One manifest per unit, `$HOLON_HOME/bylaws/units/<slug>.toml`:

    charter  = "what this unit is for"
    category = "service"          # "surface" = a harness-level capability (browser, channels)
    tier     = "foundation"       # free-form documentation today
    roots    = ["~/data/vault"]   # read scope granted to departments that pool this unit
    owner    = "platform-ops"     # the department that keeps a surface true (C4)
    pool_of  = ["career"]         # departments that pool it (charter+services render in their
                                  # briefs; roots join their reach; writes go through msg request);
                                  # ["*"] = every brief, org-wide
    consumed = ["vault:kosmos"]   # services this unit's work calls (bind.service checks them)

    [[services]]
    id      = "kosmos"
    command = "kosmos --query"    # the executable entry point, verbatim
    accepts = "a research query"
    returns = "sources and notes"
    effect  = "read"              # read | write: a write route is offered to act stages only
    note    = "beats ad-hoc web search"   # when to prefer this route (rendered in briefs)
    args    = "--query <text>"    # the call shape, for a runtime's tool index
    output  = "text"              # toon | json | text
    health  = "kosmos --version"  # a command probing this route's liveness ([smoke])

`holon bylaws sync` (chair) regenerates the database rows from the manifests and is the only
writer; `holon lint` (bind.service, bind.pool) checks references; `holon bylaws emit` derives
`bylaws/registry.md`. Nothing here flows back into the files: the manifests are the source.
"""

from __future__ import annotations

import json
import re
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from holon.core.db import now, write_txn
from holon.core.vocab import CHAIR


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load(home) -> tuple[dict[str, dict], list[str]]:
    """Every parseable manifest, and the findings for those that are not. Findings are plain
    strings: lint reports them, sync refuses to touch the database while any exist."""
    units: dict[str, dict] = {}
    problems: list[str] = []
    d = home.bylaws_units_dir
    if not d.is_dir():
        return units, problems
    for f in sorted(d.glob("*.toml")):
        try:
            m = tomllib.loads(f.read_text())
        except (tomllib.TOMLDecodeError, OSError) as e:
            problems.append(f"{f.name}: {e}")
            continue
        slug = f.stem
        services = m.get("services") or []
        if not isinstance(m.get("charter", ""), str) or not services:
            problems.append(f"{f.name}: needs a charter and at least one [[services]] entry")
            continue
        for s in services:
            if not s.get("id") or not s.get("command"):
                problems.append(f"{f.name}: a service needs an id and a command (its entry point)")
        units[slug] = {"charter": m.get("charter", ""), "category": m.get("category", ""),
                       "tier": m.get("tier", ""), "roots": m.get("roots") or [],
                       "owner": m.get("owner", ""),
                       "pool_of": m.get("pool_of") or [], "consumed": m.get("consumed") or [],
                       "services": services, "mtime": _mtime(f), "file": f.name}
    return units, problems


# The manifest directory's former name (the layer was "genome" before the bylaws rename). A
# manifest left there is invisible to load() and to lint: the capability layer's only unit
# disappears from the tables on the next sync, with no error and no finding. The doctor names
# it; sync refuses to empty the tables while it is there.
LEGACY_MANIFEST_DIRS = ("genome/units",)


def orphan_manifests(home) -> list[str]:
    """Manifest files that exist outside the manifest directory — the layer's old path, or a
    directory that was renamed. Each is a capability the kernel cannot see."""
    out: list[str] = []
    for rel in LEGACY_MANIFEST_DIRS:
        d = home.root / rel
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.toml")):
            out.append(f"{rel}/{f.name}")
    return out


def sync(k) -> dict:
    """Regenerate the units tables from the manifests (the only writer). Refuses on any parse
    problem: a half-parsed bylaws is worse than a stale one. And refuses to empty the tables
    when the manifest directory yields nothing while the tables hold units — the capability
    layer's own deletion is never a sync's job (a manifest at the wrong path would take it)."""
    units, problems = load(k.home)
    if problems:
        raise ValueError("; ".join(problems))
    orphans = orphan_manifests(k.home)
    current = k.wconn.execute("SELECT count(*) FROM units").fetchone()[0]
    if orphans and not units:
        raise ValueError(
            f"refusing to empty the units tables: {len(orphans)} manifest(s) exist outside "
            f"{k.home.bylaws_units_dir.name}/ ({', '.join(orphans)}) — move them there, or remove them "
            "if the capability is retired")
    if not units and current:
        raise ValueError(
            f"refusing to empty the units tables: {current} unit(s) are synced but "
            f"{k.home.bylaws_units_dir} holds no manifest — moving a manifest out of the directory "
            "would silently drop the capability layer; restore it or sync from a manifest directory "
            "that still has it")
    n_services = sum(len(u["services"]) for u in units.values())
    with write_txn(k.wconn):
        k.wconn.execute("DELETE FROM units")
        k.wconn.execute("DELETE FROM unit_services")
        k.wconn.execute("DELETE FROM unit_edges")
        for slug, u in units.items():
            k.wconn.execute("INSERT INTO units (slug, charter, category, tier, roots, owner, synced_at)"
                            " VALUES (?,?,?,?,?,?,?)",
                            (slug, u["charter"], u["category"], u["tier"], json.dumps(u["roots"]),
                             u.get("owner"), now()))
            for s in u["services"]:
                k.wconn.execute("INSERT OR REPLACE INTO unit_services (slug, service, command, accepts,"
                                " returns, effect, args, output, note, health) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                (slug, s["id"], s.get("command"), s.get("accepts"), s.get("returns"),
                                 s.get("effect"), s.get("args"), s.get("output"),
                                 s.get("note"), s.get("health")))
            for d in u["pool_of"]:
                k.wconn.execute("INSERT OR REPLACE INTO unit_edges VALUES (?,?,?)", (slug, "pool", d))
            for c in u["consumed"]:
                k.wconn.execute("INSERT OR REPLACE INTO unit_edges VALUES (?,?,?)", (slug, "consumes", c))
    return {"units": len(units), "services": n_services}


def validate(k) -> list[str]:
    """bind.service and bind.pool (lint): every consumed ref names a service a unit offers;
    every pooled unit names a department that exists; and the sync is not stale."""
    units, problems = load(k.home)
    problems = [f"manifest: {p}" for p in problems]
    if not k.wconn.execute("SELECT count(*) FROM units").fetchone()[0] and not units:
        return problems
    known_depts = {d["slug"] for d in k.org.depts()}
    slugs = set(units)
    for slug, u in units.items():
        if u["category"] == "surface":
            for s in u["services"]:
                if not s.get("effect") or not s.get("note"):
                    problems.append(f"{u['file']}: a surface service needs effect (read|write) and note "
                                    "(when to prefer this route) — lint's surface rule (C1)")
        for d in u["pool_of"]:
            if d != "*" and d not in known_depts:
                problems.append(f"{u['file']}: pools '{d}', which is not a department (bind.pool)")
        for c in u["consumed"]:
            other, _, service = c.rpartition(":")
            if not other or other not in slugs or \
                    not any(s["id"] == service for s in units[other]["services"]):
                problems.append(f"{u['file']}: consumes '{c}', which no unit offers (bind.service)")
    stale = [(slug, u) for slug, u in units.items() for _ in [0]
             if _synced_at(k, slug) and _synced_at(k, slug) < u["mtime"]]
    if stale:
        slug, u = stale[0]
        problems.append(f"bylaws stale: {slug}'s manifest is newer than the last sync "
                        f"(run `holon bylaws sync`)")
    return problems


def _synced_at(k, slug: str) -> str | None:
    r = k.wconn.execute("SELECT synced_at FROM units WHERE slug=?", (slug,)).fetchone()
    return r[0] if r else None


def pooled_by(conn, dept_slug: str) -> list[dict]:
    """The units a department pools (U2): charter and services render in its briefs, and their
    roots join its reach. Writes to a unit still go through `msg request` — pooling grants
    presence and reads, never the pen."""
    out = []
    for u in conn.execute(
            "SELECT u.slug, u.charter, u.roots FROM unit_edges e JOIN units u ON u.slug = e.slug"
            " WHERE e.kind='pool' AND e.other=?", (dept_slug,)).fetchall():
        services = conn.execute("SELECT service, command, accepts, returns FROM unit_services"
                                " WHERE slug=?", (u["slug"],)).fetchall()
        out.append({"slug": u["slug"], "charter": u["charter"],
                    "roots": json.loads(u["roots"] or "[]"),
                    "services": [dict(s) for s in services]})
    return out


def capabilities_for(conn, dept_slug: str | None) -> list[dict]:
    """What a run may use (C2): the department's pooled units plus every org-wide surface
    (`pool_of = ["*"]` — the browser, comms — so department-less work still sees them, C-G4),
    surfaces first. Each service carries its route `note` (which to prefer) and its `health`
    command (which is down), and its `effect` — a write route is offered to act stages only."""
    out = []
    for u in conn.execute(
            "SELECT u.slug, u.charter, u.owner, u.category FROM unit_edges e JOIN units u ON u.slug=e.slug"
            " WHERE e.kind='pool' AND (e.other=? OR e.other='*') ORDER BY (u.category<>'surface'), u.slug",
            (dept_slug,)).fetchall():
        services = conn.execute(
            "SELECT service, command, accepts, returns, effect, note, health, args, output"
            " FROM unit_services WHERE slug=? ORDER BY service", (u["slug"],)).fetchall()
        out.append({"slug": u["slug"], "charter": u["charter"], "owner": u["owner"],
                    "category": u["category"], "services": [dict(s) for s in services]})
    return out


def emit(k) -> Path:
    """U3: derive the registry (registry.md) from the synced tables — the living architecture's
    role in the Holon grain: the unit tree, the services each offers, and the consumption graph.
    The manifests are the source; this is a view, never a register."""
    lines = ["# The organization's units (derived — regenerate with `holon bylaws emit`)", ""]
    units = k.wconn.execute("SELECT * FROM units ORDER BY slug").fetchall()
    for u in units:
        pool = [r["other"] for r in k.wconn.execute(
            "SELECT other FROM unit_edges WHERE slug=? AND kind='pool'", (u["slug"],))]
        consumed = [r["other"] for r in k.wconn.execute(
            "SELECT other FROM unit_edges WHERE slug=? AND kind='consumes'", (u["slug"],))]
        lines.append(f"## {u['slug']} ({u['tier'] or 'unit'})\n")
        lines.append(f"{u['charter']}\n")
        lines.append((f"owner: {u['owner']} · " if u["owner"] else "")
                     + f"pooled by: {', '.join(pool) or 'nobody'} · consumes: {', '.join(consumed) or 'nothing'}\n")
        for s in k.wconn.execute("SELECT * FROM unit_services WHERE slug=?", (u["slug"],)):
            lines.append(f"- **{u['slug']}:{s['service']}** — `{s['command']}`"
                         f" (accepts: {s['accepts'] or '?'}; returns: {s['returns'] or '?'}"
                         + (f"; effect {s['effect']}" if s["effect"] else "")
                         + (f"; prefer: {s['note']}" if s["note"] else "")
                         + (f"; health `{s['health']}`" if s["health"] else "") + ")")
        lines.append("")
    out = k.home.bylaws_registry
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    return out


# ── the pooling workflow (A10, 2026-10-22 audit) ────────────────────────────
# The units layer had the mechanism and no workflow: one manifest pools a capability, renders it
# into every pooler's brief, fences its binaries — and nothing ever turned "a script a department
# keeps running" into one. The live instance held a single manifest while ventures' scripts landed
# wherever the venture kept them. `draft` proposes the manifest into a drafts area that changes no
# state; `promote` (the Chair) moves it into the manifest directory after the same validation lint
# runs — the KB's blueprint draft/promote shape, one level down.

_DRAFT_VOCAB = re.compile(r"^[a-z][a-z0-9-]{1,39}$")
_SERVICE_VOCAB = re.compile(r"^[a-z][a-z0-9-]{0,39}$")   # one-letter service ids are fine (kb's rule)


def drafts_dir(home) -> Path:
    return home.bylaws_units_dir.parent / "drafts"


def _quote(s: str) -> str:
    return '"' + s.replace('\\', '\\\\').replace('"', '\\"') + '"'


def draft(k, slug: str, charter: str, service: str, command: str, accepts: str = "",
          returns: str = "", note: str = "", effect: str = "read", args: str = "",
          health: str = "", category: str = "service", tier: str = "proposed",
          pool_of: list[str] | None = None, owner: str = "", roots: list[str] | None = None) -> Path:
    """An employee's proposal of a unit, written as a manifest into the drafts area. Changes no
    state: the units tables, the reach and the registry are untouched until the Chair promotes
    it. One service is required (the observed script); more can be added to the file by hand."""
    if not _DRAFT_VOCAB.match(slug or ""):
        raise ValueError(f"unit name {slug!r}: lowercase letters, digits and hyphens")
    for name, v in (("service", service), ("command", command), ("charter", charter)):
        if not (v or "").strip():
            raise ValueError(f"{name} is empty: a draft says what it does and how it is called")
    if not _SERVICE_VOCAB.match(service):
        raise ValueError(f"service id {service!r}: lowercase letters, digits and hyphens")
    body = [f"charter = {_quote(charter)}", f"category = {_quote(category)}",
            f"tier = {_quote(tier)}",
            f"roots = [{', '.join(_quote(r) for r in roots or [])}]",
            f"pool_of = [{', '.join(_quote(p) for p in pool_of or [])}]",
            "consumed = []", ""]
    if owner:
        body.append(f"owner = {_quote(owner)}")
        body.append("")
    body += ["[[services]]", f"id = {_quote(service)}", f"command = {_quote(command)}"]
    for key, v in (("accepts", accepts), ("returns", returns), ("note", note),
                   ("effect", effect), ("args", args), ("health", health)):
        if v:
            body.append(f"{key} = {_quote(v)}")
    d = drafts_dir(k.home)
    d.mkdir(parents=True, exist_ok=True)
    out = d / f"{slug}.toml"
    out.write_text("\n".join(body) + "\n")
    return out


def promote(k, slug: str, principal: str) -> Path:
    """The Chair's ruling: move a draft into the manifest directory. The same validation lint
    runs first (a unit that would fail its first lint does not land), and a name that is already
    a unit is refused — the drafts area must never become a second, unvalidated manifest
    directory."""
    if principal != CHAIR and not principal.startswith("chair:"):
        raise PermissionError(f"promoting a unit is the Chair's call, not {principal!r}")
    f = drafts_dir(k.home) / f"{slug}.toml"
    if not f.is_file():
        raise LookupError(f"no draft {slug}")
    if (k.home.bylaws_units_dir / f"{slug}.toml").exists():
        raise ValueError(f"a unit {slug} already exists: two manifests of one capability is the "
                         "redundancy the drafts area exists to prevent")
    k.home.bylaws_units_dir.mkdir(parents=True, exist_ok=True)
    staged = k.home.bylaws_units_dir / f"{slug}.toml"
    staged.write_text(f.read_text())
    problems = validate(k)
    if problems:
        staged.unlink()
        raise ValueError("; ".join(problems))
    f.unlink()
    return staged
