"""Policy.json: the per-run reach compiled at claim (boundary doc §4.3, H3+H5).

The kernel writes one policy document beside the brief. A harness may enforce it;
a harness with its own enforcement layer MUST (its adapter's posture configs are
replaced by it in phase C of the runtime plan). The document names what this run
may touch, and the budget it may spend; a call outside it is refused by the
harness and reported in `result.refusals[]` — the kernel treats refusals as
blockers (rework or escalation), never as breaches.

Compiled from the same sources the brief's "Your reach" section and the sandbox
adapters already use — [scope] (plus the department's own), the approved plan's
declared writes, and [budget]'s per-run token/cost caps — so there is exactly one
definition of a run's reach, rendered three ways (brief prose, sandbox config,
machine policy).
"""

from __future__ import annotations

import json
from pathlib import Path

POLICY_VERSION = 1


def compile_policy(node: dict, work: dict | None, scope: dict | None, plan: dict | None,
                    budget: dict | None) -> dict:
    """Build the policy document for one run.

    `node` is the claimed stage (grade, stage, lane); `work` its work (workdir);
    `scope` the department's effective reach (`Kernel.scope(slug)`); `plan` the
    approved plan output body for an execute stage (its `writes` are what the
    approver saw); `budget` the per-run caps (`tokens`/`cost`, None = uncapped).
    """
    stage = node["stage"]
    posture = "act" if stage == "execute" else "observe"
    workdir = (work or {}).get("workdir") or None
    roots = [workdir] if workdir else []
    roots += (scope or {}).get("roots", [])
    commands = (scope or {}).get("commands", []) or []
    if posture == "act":
        commands += (scope or {}).get("act_commands", []) or []
    writes = list((plan or {}).get("writes") or []) if posture == "act" else []
    return {
        "version": POLICY_VERSION,
        "grade": node.get("grade"),
        "stage": stage,
        "posture": posture,
        "roots": sorted({r for r in roots if r}),
        "writes": writes,
        "commands": sorted(set(commands)),
        "deny": [],
        "budget": {"tokens": (budget or {}).get("tokens"), "cost": (budget or {}).get("cost")},
    }


def write_policy(run_dir: Path, policy: dict) -> Path:
    p = run_dir / "policy.json"
    p.write_text(json.dumps(policy, indent=2))
    try:
        p.chmod(0o640)   # the enforcing harness must read it (CONTRACT §10, H-07)
    except OSError:  # pragma: no cover - mode is advisory outside isolation
        pass
    return p


def per_run_budget(config: dict, dept_slug: str | None, employee_id: str | None) -> dict:
    """H5: resolve the per-run caps from [budget] — org defaults, then the department's
    override, then the employee's. `tokens_per_run` / `cost_per_run` (0 or absent = no cap).

    Kept in the kernel's own units: tokens are model-agnostic, cost is the invoice
    currency. The harness stops the run at the cap and says so in its result; the
    kernel's own run caps (`max_running_per_dept`) are unchanged.
    """
    b = config.get("budget", {})
    cap = {k: b.get(k, 0) for k in ("tokens_per_run", "cost_per_run")}
    if dept_slug:
        own = (b.get("dept") or {}).get(dept_slug) or {}
        cap.update({k: own.get(k, cap[k]) for k in cap})
    if employee_id:
        own = (b.get("employee") or {}).get(employee_id) or {}
        cap.update({k: own.get(k, cap[k]) for k in cap})
    return {
        "tokens": cap["tokens_per_run"] or None,
        "cost": None if cap["cost_per_run"] in (0, 0.0, None) else float(cap["cost_per_run"]),
    }
