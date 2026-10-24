"""M4: the venture department builder — one command reads a venture's four artifacts.

A venture is `_org.yaml`, `cron-jobs/*.md`, `smoke.yaml` and `scripts/`, and each
maps to a different kernel feature. Nothing read all four, so a venture department had
to be built by hand — which is why a wave was per-pool toil instead of configuration.

`holon venture import <path> --dept <slug>` reads all four at once:
- `_org.yaml` → the department's charter, its profile, and its scope roots;
- `cron-jobs/*.md` → routines, through the repaired importer (nothing silently dropped);
- `smoke.yaml` → the department's checks (YAML in, the kernel's TOML out);
- `scripts/` → the script lane's commands.

Dry-run by default. `--apply` creates the department and its routines, leaves the
routines **unapproved**, and computes each routine's outside-reach check on the
*fidelity-repaired* text — so the Chair's standing rule sees the protocol's own words.
"""

from __future__ import annotations

import json
from pathlib import Path

from holon.core.db import now

# The pool's own working directories: the department's reach is these, and nothing above
# them. A venture's data, kb, templates, logs and screenshots are its work; its scripts
# and protocols are its code.
#
# This is the FALLBACK, not the reach. A pool that declares `directories:` in its own
# `_org.yaml` names its real shape there — `pioneer-qf` declares `sub-systems/`, `scratch/`,
# `bin/` and `core/`, and none of the first three is in this tuple — so the declared list is
# read first (G1 / A2, measured 2026-10-24). `references` is the reverse case: it sits in this
# tuple while no pool's declared list names it, which is why the fallback survives rather than
# being deleted.
POOL_DIRS = ("data", "kb", "templates", "logs", "screenshots", "references", "core")


def _declared_dirs(node: object, out: list[str], prefix: str = "") -> None:
    """Every directory a pool's `directories:` tree declares, recursing into `sub_dirs`.

    The key is a nested mapping: a directory may carry a `sub_dirs` mapping of its own
    (`pioneer-qf`'s `core/` holds `decisions/`, `regressions/` and `worklog/`), and a
    reach that names the parent but not the children under-declares what the pool really
    has. Rules attached to each entry (`allow_any`, `require_agents`, `frontmatter_required`)
    are the lint's business, not the reach's, so they are stepped over.

    Each name is emitted as a path RELATIVE TO THE POOL, not a bare name: a `sub_dirs`
    child of `core/` is `core/decisions`, which is the only form `pool / d` can check.
    Emitting flat names made `pool/core/decisions` unreachable, which is how the first
    version of this function read its own test's failure.
    """
    if not isinstance(node, dict):
        return
    for name, spec in node.items():
        # a key naming a file (an inline mapping of its rules) is not a directory; the
        # trailing slash is what `_org.yaml` uses to say "this one is"
        rel = prefix + str(name).rstrip("/")
        if str(name).endswith("/"):
            out.append(rel)
        if isinstance(spec, dict):
            _declared_dirs(spec.get("sub_dirs"), out, rel + "/")


def pool_roots(v: dict) -> list[str]:
    """The department's reach, from the pool's own `directories:` where it declares any.

    `POOL_DIRS` was the only source, and it is a fixed tuple: measured 2026-10-24 against
    `pioneer-qf`, it listed `data`, `logs` and `core` while the pool actually holds
    `sub-systems/`, `scratch/`, `bin/` and `core/` — so three real working directories named
    their own `_org.yaml` were missing from the reach's list. The pool root is prepended by
    the caller, so the reach still COVERED them (that is why this was a precision gap rather
    than a reach gap), but a list that names three missing directories and three absent ones
    is not a description anyone should trust.

    The declared list is used when the pool has one, `POOL_DIRS` otherwise, and both are
    filtered to directories that exist: a declaration is a claim about a layout, not a
    promise the directory is there today.
    """
    pool = Path(v["pool"])
    org = v.get("org") if isinstance(v.get("org"), dict) else {}
    declared: list[str] = []
    _declared_dirs(org.get("directories"), declared)
    names = declared or list(POOL_DIRS)
    roots, seen = [], set()
    for d in names:
        if d in seen:
            continue
        seen.add(d)
        if (pool / d).is_dir():
            roots.append(str(pool / d))
    return roots


def _yaml(path: Path) -> dict:
    """The pool's YAML, read without a YAML dependency.

    The kernel is stdlib only (AGENTS.md law 1), so this reads the pool's own YAML
    subset directly: mappings, lists of mappings, and scalars. Anything it does not
    understand raises rather than guessing — a silently misread charter is worse than a
    refused one.
    """
    return _mini_yaml(path)


def _unescape(text: str) -> str:
    """A YAML double-quoted string's escapes: `\\uXXXX`, `\\xXX`, `\\n`, `\\t`, `\\\\`, `\\"`.

    The pools use them heavily — an em dash written as `\\u2014` and a multiplication sign
    as `\\xD7` are in charters and in checks' reasons — so a reader that leaves them raw
    shows the operator escape sequences instead of the text.
    """
    if "\\" not in text:
        return text
    out, i = [], 0
    while i < len(text):
        ch = text[i]
        if ch != "\\" or i + 1 >= len(text):
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1]
        # `\x` takes TWO hex digits and `\u` takes FOUR. Reading four for both left every `\x`
        # escape raw — and the pools write a multiplication sign as `\xD7`, so a charter read
        # that way showed the operator's escape sequence instead of the character.
        width = {"u": 4, "x": 2}.get(nxt)
        if width and i + 2 + width <= len(text):
            try:
                out.append(chr(int(text[i + 2:i + 2 + width], 16)))
                i += 2 + width
                continue
            except ValueError:
                pass
        if nxt == " ":
            # an escaped space: YAML's way of writing a space that would end a word
            out.append(" ")
            i += 2
            continue
        out.append({"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}.get(nxt, "\\" + nxt))
        i += 2
    return "".join(out)


def _mini_yaml(path: Path) -> dict:
    """A small YAML reader for the pool's own files.

    The subset the pools use: nested mappings, lists of scalars, lists of mappings, and
    scalars (quoted or bare, with `true`/`false`/numbers). Anything it does not
    understand raises rather than guessing — a silently misread charter is worse than a
    refused one.
    """
    lines: list[tuple[int, str]] = []
    for raw in path.read_text().splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append((len(raw) - len(raw.lstrip()), raw.strip()))

    def scalar(text: str):
        text = text.strip()
        if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
            # Only a DOUBLE-quoted scalar carries escapes: a single-quoted one keeps its
            # backslashes literally (YAML's only escape there is '' for a quote). Unescaping
            # a single-quoted scalar rewrites the text — a pool's one-line
            # `cmd: '… \"healthy\" …'` would import as `"healthy"`, a command that is
            # not the one the operator approved. The continued-line path below gates on the
            # same distinction; this is its single-line sibling, and the live corpus found
            # the gap (architectural-leads, 2026-10-22).
            return _unescape(text[1:-1]) if text[0] == '"' else text[1:-1].replace("''", "'")
        if text in ("true", "True"):
            return True
        if text in ("false", "False"):
            return False
        if text in ("null", "~", ""):
            return None
        try:
            return int(text)
        except ValueError:
            pass
        try:
            return float(text)
        except ValueError:
            return _unescape(text)

    def inline(text: str):
        """A `[a, b]` flow list, or a scalar."""
        text = text.strip()
        if text.startswith("[") and text.endswith("]"):
            return [scalar(v) for v in text[1:-1].split(",") if v.strip()]
        return scalar(text)

    def value_at(i: int, indent: int, first: str) -> tuple[object, int]:
        """A key's value, which may continue on the following lines.

        The pools wrap long values:
            cmd: python3 ... --help >
              /dev/null
        and quote them across lines with a trailing backslash. Both are one value.
        """
        text = first.strip()
        # a block scalar: `key: |`, `key: >` or `key: >-`. Everything indented past `indent`
        # is one value. A folded block (`>`) joins its lines with spaces; a literal block (`|`)
        # keeps them. The chomp indicator is the second character: `-` strips the final newline,
        # `+` keeps every trailing one, and none clips exactly one — YAML's own rule, so a value
        # read here compares equal to a library's parse of the same file.
        if text[:1] in ("|", ">") and set(text[1:]) <= set("-+"):
            body: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j][0] > indent:
                body.append(lines[j][1])
                j += 1
            if not body:
                return "", j
            joined = (" " if text[0] == ">" else "\n").join(body)
            return (joined if text[1:2] == "-" else joined + "\n"), j
        # a quoted value continued on the following lines (single or double quotes)
        quoted_open = (text.endswith("\\") or text.startswith('"') or text.startswith("'")) and not (
            len(text) > 1 and text[0] == text[-1] and text[0] in "\"'")
        if quoted_open:
            # Is the value double- or single-quoted? The distinction decides what a
            # trailing backslash means at the end of a continued line, so it is asked
            # once, on the text as written, before any of it is stripped.
            dq = text.lstrip()[:1] == '"'
            # In a double-quoted scalar a single trailing backslash escapes the line
            # break: YAML drops the marker AND the fold space, joining with nothing. In a
            # single-quoted scalar a backslash is literal — it stays, and the fold joins
            # with a space. Every other line ends its fold with a space. Stripping the
            # marker indiscriminately rewrote all three.
            def ends_in_escaped_break(s: str) -> bool:
                return dq and s.endswith("\\") and not s.endswith("\\\\")
            parts = [text]
            j = i + 1
            while j < len(lines) and lines[j][0] > indent:
                piece = lines[j][1]
                if piece.startswith("- "):
                    break  # a list, not a continuation
                parts.append(piece)
                j += 1
            # the first line's own trailing whitespace is never part of the value
            joined = parts[0].rstrip()
            for piece in parts[1:]:
                if ends_in_escaped_break(joined):
                    # the previous line's marker escapes the break: it is dropped and
                    # this one joins directly to it, with no fold space at all
                    joined = joined[:-1].rstrip() + piece.strip()
                else:
                    joined += " " + piece.strip()
            if len(joined) > 1 and joined[0] == joined[-1] and joined[0] in "\"'":
                joined = joined[1:-1]
            # the escapes (\uXXXX, \xXX) only in a double-quoted scalar, then the escaped
            # space YAML writes to keep a leading space at a fold.
            if dq:
                joined = _unescape(joined)
                joined = joined.replace("\\ ", " ")
            return joined, max(j, i + 1)
        if text == "":
            return None, i + 1
        # a plain scalar continued on the next, deeper lines (a command wrapped over
        # several lines is one value)
        if i + 1 < len(lines) and lines[i + 1][0] > indent and not lines[i + 1][1].startswith("- "):
            parts = [text]
            j = i + 1
            while j < len(lines) and lines[j][0] > indent and not lines[j][1].startswith("- "):
                nxt = lines[j][1]
                # a continuation is any deeper line that is not a key of its own block
                if ":" in nxt and nxt.split(":", 1)[0].replace("_", "").isalnum():
                    break
                parts.append(nxt)
                j += 1
            if len(parts) > 1:
                return (_unescape(" ".join(parts)), j)
        return inline(text), i + 1

    def block(start: int, indent: int):
        """The block at `start` with the given indentation: a mapping or a list."""
        # a list?
        if start < len(lines) and lines[start][1].startswith("- "):
            items: list = []
            i = start
            while i < len(lines) and lines[i][0] == indent and lines[i][1].startswith("- "):
                rest = lines[i][1][2:].strip()
                if ":" in rest and not rest.startswith("{"):
                    # a mapping item: `- name: x` continues on the following lines
                    key, _, value = rest.partition(":")
                    item = {key.strip(): inline(value)}
                    i += 1
                    # the item's own value may continue on the following lines
                    if value.strip() == "" and i < len(lines) and lines[i][0] > indent \
                            and not lines[i][1].startswith("- "):
                        item[key.strip()], i = value_at(i, lines[i][0], "")
                    # its own keys, which are deeper than the `- ` marker
                    while i < len(lines) and lines[i][0] > indent:
                        if lines[i][1].startswith("- "):
                            break
                        k2, _, v2 = lines[i][1].partition(":")
                        if not k2:
                            break
                        k2, v2 = k2.strip(), v2.strip()
                        if v2 == "":
                            # a nested block: a list (which may sit at this key's own
                            # indent) or a mapping
                            nxt = lines[i + 1] if i + 1 < len(lines) else None
                            if nxt and (nxt[0] > lines[i][0]
                                         or (nxt[0] == lines[i][0] and nxt[1].startswith("- "))):
                                item[k2], i = block(i + 1, nxt[0])
                            else:
                                item[k2] = None
                                i += 1
                        else:
                            item[k2], i = value_at(i, lines[i][0], v2)
                    items.append(item)
                    continue
                # a plain scalar item
                items.append(scalar(rest))
                i += 1
            return items, i
        # a mapping: a key at this indent, until a line that is not one
        out: dict = {}
        i = start
        while i < len(lines) and lines[i][0] == indent:
            line = lines[i][1]
            if line.startswith("- "):
                # a list belonging to the key just read (YAML allows it at the same indent)
                break
            if ":" not in line:
                raise ValueError(f"{path}: cannot read {line!r} without a YAML library")
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            if value == "":
                # a list may sit at the same indent as its key (YAML allows it), or deeper
                nxt = lines[i + 1] if i + 1 < len(lines) else None
                if nxt and (nxt[0] > indent or (nxt[0] == indent and nxt[1].startswith("- "))):
                    child, i = block(i + 1, nxt[0])
                    out[key] = child
                    continue
                out[key] = None
                i += 1
                continue
            out[key], i = value_at(i, indent, value)
        return out, i

    if not lines:
        return {}
    out, _ = block(0, lines[0][0])
    return out


def _would_refuse(k, rows: list[dict], already: list[dict]) -> list[dict]:
    """What the apply would decline, predicted without writing anything.

    The apply's refusal lives in `add_routine`: a job the classifier calls `script` with no
    `--command` is refused, because a script lane without a command cannot run. The dry run skipped
    that call entirely, so a survey of a real pool reported `skipped: []` while the apply filed 3 of
    4 and named nothing about the fourth. Predicting it costs one classification call and makes the
    survey say what the apply would say — which is the whole point of a survey."""
    out = list(already)
    named = {s.get("title") for s in out}
    # R-1's half of the prediction: what the APPLY would refuse, forecast without writing. The
    # apply skips a protocol that declares a foreign runtime; a survey that never calls it must
    # say so, or the pool reports clean and gets dropped — the silence twice measured before.
    #
    # A row the status tuple ALREADY skips is not exempt from this prediction, and that is the
    # measured regression this loop now guards: `if r["title"] in named: continue` used to skip it,
    # so a protocol that declared both a retired status and a cadence no unattended lane can
    # justify reported only "the protocol declares itself paused" — the refusal that was the whole
    # reason anyone looked at the row was swallowed. Measured 2026-10-23 on the real corpus, where
    # BOTH cadence refusals (`reddit-karma-lane-2h-draft-post`, 12 fires a day, and
    # `dad-arch-strategist-listener`, 26) declare a retired status as well and vanished behind it.
    # The status skip is real and stays first; a refusal found alongside it is appended to that
    # row's reason, so one row can carry both and neither is lost.
    for r in rows:
        skipped_by_status = r["title"] in named
        if skipped_by_status:
            row = next(s for s in out if s["title"] == r["title"])
        if not skipped_by_status and r.get("foreign_runtime"):
            out.append({"title": r["title"], "reason": "refused: relevance — this job declares a runtime "
                                                  "this kernel does not serve (`runtime: "
                                                  f"{r['provenance']['fields'].get('runtime')}`); a proposal "
                                                  "comes from relevance to this organization, not from the "
                                                  "existence of a legacy system's protocol files"})
            continue
        # A3: the five checks that are pure argument validation, asked BEFORE the classifier.
        # `add_routine` raises eight times, and these five depend on nothing but the routine's own
        # fields — a trigger named exactly once, an event spelled `event:<pattern>`, a cron that
        # parses, a lane and a command that agree. They were unreportable because they lived inside
        # `add_routine`, which a dry run never calls: a pool whose protocols all failed them
        # reported `protocols: 2, skipped: []` against an apply that made 0. Measured 2026-10-24,
        # exactly these five are functions of the arguments, and the other three depend on the
        # decision model a dry run may not hold (see service.py `_validate_routine_args`).
        #
        # A row declaring no trigger at all is NOT refused here. `legacy_cron` gives a protocol
        # with no `schedule:` an empty `cron`, and the apply's own call passes that to
        # `add_routine`, which raises "exactly one" and is caught below as an ordinary refusal.
        # The dry run must not refuse a row the apply only warns about, and the suite's own guard
        # (`test_absence_is_not_a_declaration_of_pause`) carries the other half of that rule, so
        # the check runs only where the row declares a trigger.
        if r.get("cron") or r.get("event"):
            try:
                k._validate_routine_args(r["title"], r["intent"], cron=r.get("cron"),
                                         event=r.get("event"), lane=r.get("lane"),
                                         command=r.get("command"))
            except ValueError as e:
                refusal = f"refused: arguments — {e}"
            else:
                refusal = None
        else:
            refusal = None
        if refusal is None:
            try:
                cls = k.classify(r["title"], r["intent"], r.get("command"))
            except Exception as e:  # a classifier that is down is not a reason to hide the refusal
                cls = {"lane": None, "error": str(e)[:80]}
            if cls.get("lane") == "script" and not r.get("command"):
                refusal = (f"this job looks deterministic (script, "
                           f"{(cls.get('confidence') or 0):.0%}): give it as a script with "
                           f"--command, or say why an agent must do it")
            elif cls.get("lane") is None and not r.get("command") and k._fires_often(r.get("cron") or ""):
                # The other refusal, and the one the model's silence hides: with no decision model
                # there is no lane to compare against, and a job that fires every few minutes as a
                # full routine buys a body on every firing (288 a day for a */5 cron). The apply
                # refuses it, so the survey must too — otherwise the dry run again reports a clean
                # pool that the apply drops.
                refusal = (f"no decision model is available and this job fires on "
                           f"'{r.get('cron')}' ({k._per_day(r.get('cron') or '')} times a day): "
                           f"a full lane would dispatch a body on every firing. Configure the "
                           f"classifier, give it as a script with --command, or say --why an "
                           f"agent must do it")
        if refusal is None:
            continue
        if skipped_by_status:
            # Both are true of the same row. The status skip is the kernel's decision; the
            # refusal is what a reader cannot re-derive from the file, so it is appended
            # rather than replacing it.
            row["reason"] = f"{row['reason']}; ALSO: {refusal}"
        else:
            out.append({"title": r["title"], "reason": refusal})
    return out


def read_venture(pool: Path) -> dict:
    """Everything a venture's four artifacts say, in one place."""
    pool = Path(pool).expanduser().resolve()
    org = _yaml(pool / "_org.yaml") if (pool / "_org.yaml").exists() else {}
    smoke = _yaml(pool / "smoke.yaml") if (pool / "smoke.yaml").exists() else {}
    protocols = sorted((pool / "cron-jobs").glob("*.md")) if (pool / "cron-jobs").is_dir() else []
    scripts = sorted((pool / "scripts").glob("*")) if (pool / "scripts").is_dir() else []
    scripts = [s for s in scripts if s.is_file()]
    charter = ""
    dept = org.get("department") or {}
    if isinstance(dept, dict):
        charter = dept.get("charter") or ""
    if not charter:
        poolinfo = org.get("pool") or {}
        charter = (poolinfo.get("purpose") or poolinfo.get("name") or pool.name) if isinstance(poolinfo, dict) else pool.name
    checks = smoke.get("checks") if isinstance(smoke, dict) else []
    return {"pool": pool, "name": pool.name, "charter": charter, "org": org, "smoke": smoke,
            "checks": checks if isinstance(checks, list) else [],
            "protocols": protocols, "scripts": scripts,
            "services_consumed": (org.get("services_consumed") or []) if isinstance(org, dict) else []}


def venture_import(k, pool: Path, dept: str, dry_run: bool = True) -> dict:
    """Read a venture's four artifacts and (unless a dry run) make them a department.

    The dry run reads everything and writes nothing. The apply creates the department
    with its charter, its reach, its checks, and its routines — every routine
    **unapproved**, because an import is not an approval.
    """
    from holon.cli import legacy_cron
    from holon.execution import bylaws

    v = read_venture(pool)
    rows = [legacy_cron(p) for p in v["protocols"]]
    with_skills = sum(1 for r in rows if r["skills"])
    # Outside-reach is decided by the protocol's own words, not by the decision model: the model
    # is off by default, it rate-limits, and it fails — and a job the model cannot judge reads as
    # internal, which for an import means it lands enabled and scheduled. The verbs that reach
    # people outside the organization are named in the protocol itself (the same floor the
    # ordinary intake uses), so the import sees the same thing the Chair's rule means.
    outside = [r["title"] for r in rows if k._external_by_name(r["title"], r["intent"])[0]]

    # What the import is NOT doing, stated in the summary itself. An import that drops a protocol
    # and says nothing is indistinguishable from one that imported cleanly, which is how the only
    # real pool came to report `routines: 0` beside `protocols: 4`. Both runs name their skips.
    skipped = [{"title": r["title"], "reason": "the protocol declares itself paused"}
               for r in rows if r["paused"]]

    summary = {"dept": dept, "pool": str(v["pool"]), "charter": v["charter"],
               "protocols": len(rows), "protocols_with_skills": with_skills,
               "checks": len(v["checks"]), "scripts": len(v["scripts"]),
               "outside_reach": len(outside), "outside_reach_titles": outside,
               "services_consumed": [s.get("id") for s in v["services_consumed"] if isinstance(s, dict)],
               "dry_run": dry_run, "skipped": skipped}

    if dry_run:
        # The dry run must predict what the apply would decline. Measured 2026-10-21: the
        # script-lane refusal lives in `add_routine`, which the dry run never calls, so a survey
        # of a 4-protocol pool reported `skipped: []` while the apply silently filed 3 and named
        # nothing about the fourth. Two protocols refused as "looks deterministic" were invisible
        # until you committed. A survey that cannot see its own refusals is the same silence the
        # paused-status bug was, one layer up.
        summary["skipped"] = _would_refuse(k, rows, summary["skipped"])
        return summary

    # the department, with the pool's charter
    if any(d["slug"] == dept for d in k.org.depts()):
        raise ValueError(f"department {dept} already exists; an import does not replace one")
    k.org.create_dept(dept, dept.replace("-", " ").title(), v["charter"],
                       profile=bylaws.load_profile("light", k.home))

    # the reach: the pool's own declared working directories, POOL_DIRS only where it
    # declares none (A2). The pool root comes first, so a directory named inside it is reached
    # whether or not the list below names it — the list is a description, the root is the reach.
    roots = [str(v["pool"])] + pool_roots(v)

    # the checks, and the reach, into the instance's config
    text = k.home.config_file.read_text() if k.home.config_file.exists() else ""
    add = [f"\n[scope.dept.{dept}]\nroots = {json.dumps(roots)}\ncommands = []\nact_commands = []"]
    if v["checks"]:
        add.append(f"\n[smoke.dept.{dept}]")
        for c in v["checks"]:
            if not isinstance(c, dict):
                continue
            add.append(f"\n[[smoke.dept.{dept}.checks]]\nname = {json.dumps(c.get('name', ''))}\n"
                       f"cmd = {json.dumps(c.get('cmd', ''))}\n"
                       f"critical = {'true' if c.get('critical') else 'false'}\n"
                       f"reason = {json.dumps(c.get('reason', ''))}\n"
                       f"timeout = {int(c.get('timeout') or 60)}")
    k.home.config_file.write_text(text.rstrip() + "\n" + "\n".join(add) + "\n")

    # the routines: each a proposal for the Chair's approval
    made, skipped = 0, list(summary["skipped"])
    for r in rows:
        if r["paused"]:
            continue  # already named in the summary: the operator's own pause, not a failure
        # R-1: a job its own protocol places on a runtime this kernel does not serve is not this
        # organization's work. Importing it would schedule a body against data it cannot reach —
        # the live instance's two cancelled routines, exactly. Named here, not silently dropped.
        if r.get("foreign_runtime"):
            skipped.append({"title": r["title"], "reason": "refused: relevance — this job declares a runtime "
                                                      "this kernel does not serve (`runtime: "
                                                      f"{r['provenance']['fields'].get('runtime')}`): its data and "
                                                      "its clock live on another system"})
            continue
        try:
            k.add_routine(r["title"], r["intent"], dept=dept, cron=r["cron"], command=r["command"],
                          workdir=r["workdir"], deliver=r["deliver"], strict=False, imported=True)
            made += 1
        except (ValueError, LookupError) as e:
            # A protocol that cannot be imported is named, with the reason. Measured 2026-10-21:
            # the silent `continue` that used to sit here let the only importable pool report
            # `routines: 0` beside `protocols: 4` and say nothing — a migration foundation whose
            # own importer cannot tell a clean import from one that lost everything is not a
            # foundation.
            skipped.append({"title": r["title"], "reason": str(e)})

    summary.update({"routines": made, "skipped": skipped, "approved": 0, "created_at": now(),
                    "note": "every imported routine is unapproved: an import is not an approval"})
    return summary
