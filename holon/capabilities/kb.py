"""Knowledge bases: genome repositories for records that have a lifecycle.

Plan: docs/plans/2026-10-19-holon-knowledge-layer.md (phases K1 to K4). A knowledge base (KB) is one git repository
under $HOLON_HOME/kb/<slug>/, driven by the vendored project-genome CLI (holon/vendor/genome.py, pinned below). A KB is
bound to one department. Each verb runs under a per-KB lock, and each write is a commit by the seat that made it.
`--human` is refused here: a human-only move is made by the operator in the repository (`genome move --human`).
"""

from __future__ import annotations

import fcntl
import functools
import hashlib
import json
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from holon.core.paths import Home

GENOME = Path(__file__).resolve().parent.parent / "vendor" / "genome.py"
# The vendored CLI and the distribution's own scripts/genome are the SAME build: a KB's verbs and the
# repo's own lint must agree, or a KB can pass `holon kb run ... lint` and fail `scripts/genome lint`.
# The pin is the guard for that (a test asserts the two files are byte-identical).
GENOME_SHA256 = "449dfe61c94d494fc7b65e6289b725564d79999bea78b10bf4f66c5fbb587ae6"  # genome 2026.10.20-1
READ_VERBS = ("find", "view", "ask", "wake", "show", "lint")
WRITE_VERBS = ("new", "move", "set", "link")
JSON_VERBS = ("find", "ask", "show", "new")  # the verbs with a --json form
WAKE_CAP = 1500  # chars of `genome wake` per KB in a context bundle; measured in tests/test_kb.py
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,39}$")


class KbError(ValueError):
    pass


def _root(home: Home) -> Path:
    return home.root / "kb"


@functools.cache
def _genome() -> str:
    """The vendored CLI, refused unless it is the pinned build."""
    if hashlib.sha256(GENOME.read_bytes()).hexdigest() != GENOME_SHA256:
        raise KbError(f"{GENOME} does not match its pinned checksum; re-vendor the release")
    return str(GENOME)


def _run(cwd: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, _genome(), *argv], cwd=cwd, capture_output=True, text=True)


def _git(cwd: Path, *argv: str, actor: str = "holon") -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", f"user.name={actor}", "-c", "user.email=holon@localhost", *argv],
                          cwd=cwd, capture_output=True, text=True)


def _commit(repo: Path, actor: str, subject: str) -> str | None:
    _git(repo, "add", "-A", "--", ".", ":(exclude).genome")
    if _git(repo, "diff", "--cached", "--quiet").returncode == 0:
        return None
    r = _git(repo, "commit", "-q", "-m", f"{subject}\n\nby {actor}", actor=actor)
    if r.returncode:
        raise KbError(f"commit failed: {r.stderr.strip()}")
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.strip()


# ---------------------------------------------------------------- registry, scope, lock

def _registry(home: Home) -> dict:
    f = _root(home) / "registry.json"
    return json.loads(f.read_text()) if f.exists() else {}


def _save_registry(home: Home, reg: dict) -> None:
    _root(home).mkdir(parents=True, exist_ok=True)
    tmp = _root(home) / "registry.json.tmp"
    tmp.write_text(json.dumps(reg, indent=2, sort_keys=True) + "\n")
    tmp.replace(_root(home) / "registry.json")


def _entry(home: Home, slug: str) -> dict:
    reg = _registry(home)
    if slug not in reg:
        raise LookupError(f"no knowledge base {slug}")
    return reg[slug]


def _may_see(k, principal, entry: dict) -> None:
    """The Chair, or an employee of the KB's department. Nothing else."""
    if principal.kind == "chair":
        return
    if principal.kind == "employee" and k.org.employee(principal.employee_id)["dept"] == entry["dept"]:
        return
    raise PermissionError(f"this knowledge base is bound to dept {entry['dept']}")


def _require_seat(principal) -> None:
    if principal.kind not in ("chair", "employee"):
        raise PermissionError("knowledge-base drafts are for employees and the Chair")


def _require_chair(principal) -> None:
    if principal.kind != "chair":
        raise PermissionError("promoting a blueprint is the Chair's ruling")


def _is_human_flag(token: str) -> bool:
    # genome's argparse accepts any unambiguous prefix of --human (--hum), so refuse prefixes too
    name = token.split("=", 1)[0]
    return len(name) >= 3 and "--human".startswith(name)


@contextmanager
def _locked(home: Home, slug: str):
    _root(home).mkdir(parents=True, exist_ok=True)
    with (_root(home) / f".{slug}.lock").open("w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# ---------------------------------------------------------------- K1: seed and read

def _blueprint_ref(home: Home, name: str) -> str:
    """A catalog blueprint (promoted by the Chair), else a built-in genome blueprint."""
    if not SLUG.match(name):
        raise KbError(f"blueprint name {name!r}: lowercase letters, digits and hyphens")
    catalog = _root(home) / "_blueprints" / f"{name}.toml"
    if catalog.exists():
        return str(catalog)
    if _run(home.root, "blueprint", "show", name).returncode:
        raise KbError(f"no blueprint {name} (catalog or built-in)")
    return name


def seed(k, principal, blueprint: str, slug: str, dept_ref: str) -> dict:
    if not SLUG.match(slug):
        raise KbError(f"slug {slug!r}: lowercase letters, digits and hyphens")
    dept = k.org.dept(dept_ref)
    home = k.home
    if slug in _registry(home) or (_root(home) / slug).exists():
        raise KbError(f"knowledge base {slug} already exists")
    ref = _blueprint_ref(home, blueprint)
    repo = _root(home) / slug
    repo.mkdir(parents=True)
    try:
        _git(repo, "init", "-q", "-b", "main")
        r = _run(repo, "init", "--blueprint", ref)
        if r.returncode:
            raise KbError(f"genome init: {(r.stderr or r.stdout).strip()}")
        r = _run(repo, "lint")
        if r.returncode:
            raise KbError(f"genome lint: {r.stdout.strip()}")
        commit = _commit(repo, principal.actor, f"seed {slug} from {blueprint}")
    except BaseException:
        shutil.rmtree(repo, ignore_errors=True)
        raise
    reg = _registry(home)
    reg[slug] = {"dept": dept["slug"], "blueprint": blueprint,
                 "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    _save_registry(home, reg)
    k.memory.add_item(f"dept:{dept['slug']}", "artifact",
                      f"Knowledge base {slug} ({blueprint}), bound to dept {dept['slug']}: kb/{slug}",
                      provenance=principal.actor, confidence="high")
    return {"slug": slug, "blueprint": blueprint, "dept": dept["slug"], "path": str(repo), "commit": commit}


def listing(k, principal) -> dict:
    out = {}
    for slug, e in _registry(k.home).items():
        try:
            _may_see(k, principal, e)
        except PermissionError:
            continue
        out[slug] = e
    return out


# ---------------------------------------------------------------- K2: verbs under the seat policy

def run(k, principal, slug: str, verb: str, tokens: list[str]) -> str:
    home = k.home
    entry = _entry(home, slug)
    _may_see(k, principal, entry)
    if verb not in READ_VERBS + WRITE_VERBS:
        raise PermissionError(f"`{verb}` is not a knowledge-base verb; use one of {READ_VERBS + WRITE_VERBS}")
    if any(_is_human_flag(t) for t in tokens):
        raise PermissionError("human-only moves are made by the operator in the repository "
                              "(genome move --human), never through holon")
    repo = _root(home) / slug
    argv = [verb, *(["--json"] if verb in JSON_VERBS else []), *tokens]
    with _locked(home, slug):
        r = _run(repo, *argv)
        if r.returncode:
            raise KbError((r.stderr or r.stdout).strip() or f"genome {verb} exited {r.returncode}")
        if verb in WRITE_VERBS:
            _commit(repo, principal.actor, f"{verb} {' '.join(tokens)}".rstrip())
    return r.stdout


# ---------------------------------------------------------------- K3: drafts and the catalog

def _drafts(home: Home) -> Path:
    return _root(home) / "_drafts" / "blueprints"


def _check(home: Home, ref: str | Path) -> dict:
    r = _run(home.root, "blueprint", "check", str(ref))
    lines = [ln for ln in (r.stdout + r.stderr).splitlines() if ln.strip()]
    return {"pass": r.returncode == 0, "message": lines[-1] if lines else ""}


def draft(k, principal, name: str, base: str) -> dict:
    """Write a draft blueprint from a base, named `name`. It seeds nothing until the Chair promotes it."""
    _require_seat(principal)
    for n in (name, base):
        if not SLUG.match(n):
            raise KbError(f"blueprint name {n!r}: lowercase letters, digits and hyphens")
    r = _run(k.home.root, "blueprint", "show", base, "--toml")
    if r.returncode:
        raise KbError(f"no blueprint {base}")
    body, n = re.subn(r'^name = "[^"]*"', f'name = "{name}"', r.stdout, count=1, flags=re.M)
    if n != 1:
        raise KbError(f"base {base} has no [blueprint] name line")
    path = _drafts(k.home) / f"{name}.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return {"draft": name, "file": str(path), **_check(k.home, path)}


def check(k, principal, name: str) -> dict:
    _require_seat(principal)
    home = k.home
    for f in (_drafts(home) / f"{name}.toml", _root(home) / "_blueprints" / f"{name}.toml"):
        if f.exists():
            return {"blueprint": name, "file": str(f), **_check(home, f)}
    return {"blueprint": name, **_check(home, name)}


def promote(k, principal, name: str) -> dict:
    _require_chair(principal)
    home = k.home
    f = _drafts(home) / f"{name}.toml"
    if not f.exists():
        raise LookupError(f"no draft {name}")
    if _run(home.root, "blueprint", "show", name).returncode == 0:
        raise KbError(f"{name} is a built-in blueprint name")
    res = _check(home, f)
    if not res["pass"]:
        raise KbError(f"draft {name} does not pass genome blueprint check: {res['message']}")
    cat = _root(home) / "_blueprints"
    cat.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(f, cat / f"{name}.toml")
    return {"promoted": name, "file": str(cat / f"{name}.toml")}


# ---------------------------------------------------------------- K4: context

def wake_for_dept(home: Home, dept: str, cap: int = WAKE_CAP) -> str:
    """`genome wake` of each KB bound to a department, each cut to `cap` chars."""
    parts = []
    for slug, e in sorted(_registry(home).items()):
        if e["dept"] == dept and (_root(home) / slug / "genome.toml").exists():
            text = _run(_root(home) / slug, "wake").stdout.strip()
            parts.append(f"### kb/{slug}\n{text[:cap]}")
    return "\n\n".join(parts)
