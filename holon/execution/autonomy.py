"""Autonomy policy: how much the organization may decide without the Chair.

Safe by default, configurable up to fully autonomous, per department once trust is earned.

    [autonomy]                      # holon.toml: organization-wide
    mode = "supervised"             # supervised | delegated | autonomous (a preset; keys below override it)
    verify_sample_rate = 0.2

    [autonomy.dept.charter]         # one department, once it has earned it
    mode = "delegated"

Resolution: preset defaults < [autonomy] keys < [autonomy.dept.<slug>] mode < its keys.
Invariants no preset relaxes: authority never widens itself without an approver unless
`widen_approver = "auto"`; demotions always apply at once; nobody approves their own widening.
"""

from __future__ import annotations

import hashlib

PRESETS: dict[str, dict] = {
    # every widening of authority is the Chair's call; earned authority stops at G2
    "supervised": {"widen_approver": "chair", "auto_promote_cap": 2, "ceo_initial_max": 1,
                   "promote_after": 10, "audit_sample_permille": 100,
                   "verify_sample_rate": 0.2, "trusted_after": 10},
    # the CEO approves widenings; earned authority may reach G3
    "delegated": {"widen_approver": "ceo", "auto_promote_cap": 3, "ceo_initial_max": 2,
                  "promote_after": 10, "audit_sample_permille": 100,
                  "verify_sample_rate": 0.2, "trusted_after": 10},
    # earned authority widens itself up to G4; the Chair keeps holdout audits and the charter
    "autonomous": {"widen_approver": "auto", "auto_promote_cap": 4, "ceo_initial_max": 2,
                   "promote_after": 10, "audit_sample_permille": 50,
                   "verify_sample_rate": 0.2, "trusted_after": 10},
}
DEFAULT_MODE = "supervised"
WIDEN_APPROVERS = ("chair", "ceo", "auto")
INT_KEYS = ("auto_promote_cap", "ceo_initial_max", "promote_after", "audit_sample_permille", "trusted_after")


def _layer(base: dict, raw: dict) -> dict:
    out = dict(base)
    if raw.get("mode"):
        if raw["mode"] not in PRESETS:
            raise ValueError(f"autonomy mode must be one of {sorted(PRESETS)}, not {raw['mode']!r}")
        out.update(PRESETS[raw["mode"]], mode=raw["mode"])
    out.update({k: v for k, v in raw.items() if k in PRESETS[DEFAULT_MODE]})
    return out


def policy(config: dict, dept_slug: str | None = None) -> dict:
    raw = dict(config.get("autonomy", {}))
    legacy = config.get("authority", {})  # pre-autonomy instances kept their caps under [authority]
    base = {**PRESETS[DEFAULT_MODE], "mode": DEFAULT_MODE}
    p = _layer(base, {**{k: v for k, v in legacy.items() if k in base}, **raw})
    if dept_slug:
        p = _layer(p, raw.get("dept", {}).get(dept_slug, {}))
    for k in INT_KEYS:
        p[k] = int(p[k])
    p["verify_sample_rate"] = float(p["verify_sample_rate"])
    if not 0.0 <= p["verify_sample_rate"] <= 1.0:
        raise ValueError("verify_sample_rate must be between 0 and 1")
    if p["widen_approver"] not in WIDEN_APPROVERS:
        raise ValueError(f"widen_approver must be one of {WIDEN_APPROVERS}")
    if not 0 <= p["auto_promote_cap"] <= 4:
        raise ValueError("auto_promote_cap must be between 0 and 4")
    return p


def sampled(key: str, rate: float) -> bool:
    """Deterministic per key, so a re-run of the same decision gives the same answer."""
    return int(hashlib.sha256(key.encode()).hexdigest(), 16) % 1000 < round(rate * 1000)
