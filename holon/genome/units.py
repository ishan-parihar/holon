"""The genome layer (U1–U3): units and their services, declared not registered.

A **unit** is a thing with an identity the organization works through — a service its
departments consume or pool (a foundation in the legacy organism's terms), or a department's
own declared genome. One manifest per unit, `$HOLON_HOME/genome/units/<slug>.toml`:

    charter  = "what this unit is for"
    category = "service"          # free-form documentation
    tier     = "foundation"       # free-form documentation today
    roots    = ["~/data/vault"]   # read scope granted to departments that pool this unit
    pool_of  = ["career"]         # departments that pool it (charter+services render in their
                                 # briefs; roots join their reach; writes go through msg request)
    consumed = ["vault:kosmos"]   # services this unit's work calls (bind.service checks them)

    [[services]]
    id      = "kosmos"
    command = "kosmos --query"    # the executable entry point, verbatim
    accepts = "a research query"
    returns = "sources and notes"

`holon genome sync` (chair) regenerates the database rows from the manifests and is the only
writer; `holon lint` (bind.service, bind.pool) checks references; `holon genome emit` derives
`genome/registry.md`. Nothing here flows back into the files: the manifests are the source.
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path

from holon.core.db import now, write_txn


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load(home) -> tuple[dict[str, dict], list[str]]:
    """Every parseable manifest, and the findings for those that are not. Findings are plain
    strings: lint reports them, sync refuses to touch the database while any exist."""
    units: dict[str, dict] = {}
    problems: list[str] = []
    d = home.genome_units_dir
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
                       "pool_of": m.get("pool_of") or [], "consumed": m.get("consumed") or [],
                       "services": services, "mtime": _mtime(f), "file": f.name}
    return units, problems


def sync(k) -> dict:
    """Regenerate the units tables from the manifests (the only writer). Refuses on any parse
    problem: a half-parsed genome is worse than a stale one."""
    units, problems = load(k.home)
    if problems:
        raise ValueError("; ".join(problems))
    n_services = sum(len(u["services"]) for u in units.values())
    with write_txn(k.wconn):
        k.wconn.execute("DELETE FROM units")
        k.wconn.execute("DELETE FROM unit_services")
        k.wconn.execute("DELETE FROM unit_edges")
        for slug, u in units.items():
            k.wconn.execute("INSERT INTO units VALUES (?,?,?,?,?,?)",
                            (slug, u["charter"], u["category"], u["tier"], json.dumps(u["roots"]), now()))
            for s in u["services"]:
                k.wconn.execute("INSERT OR REPLACE INTO unit_services VALUES (?,?,?,?,?)",
                                (slug, s["id"], s.get("command"), s.get("accepts"), s.get("returns")))
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
        for d in u["pool_of"]:
            if d not in known_depts:
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
        problems.append(f"genome stale: {slug}'s manifest is newer than the last sync "
                        f"(run `holon genome sync`)")
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


def emit(k) -> Path:
    """U3: derive the registry (registry.md) from the synced tables — the living architecture's
    role in the Holon grain: the unit tree, the services each offers, and the consumption graph.
    The manifests are the source; this is a view, never a register."""
    lines = ["# The organization's units (derived — regenerate with `holon genome emit`)", ""]
    units = k.wconn.execute("SELECT * FROM units ORDER BY slug").fetchall()
    for u in units:
        pool = [r["other"] for r in k.wconn.execute(
            "SELECT other FROM unit_edges WHERE slug=? AND kind='pool'", (u["slug"],))]
        consumed = [r["other"] for r in k.wconn.execute(
            "SELECT other FROM unit_edges WHERE slug=? AND kind='consumes'", (u["slug"],))]
        lines.append(f"## {u['slug']} ({u['tier'] or 'unit'})\n")
        lines.append(f"{u['charter']}\n")
        lines.append(f"pooled by: {', '.join(pool) or 'nobody'} · consumes: {', '.join(consumed) or 'nothing'}\n")
        for s in k.wconn.execute("SELECT * FROM unit_services WHERE slug=?", (u["slug"],)):
            lines.append(f"- **{u['slug']}:{s['service']}** — `{s['command']}`"
                         f" (accepts: {s['accepts'] or '?'}; returns: {s['returns'] or '?'})")
        lines.append("")
    out = k.home.genome_registry
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    return out
