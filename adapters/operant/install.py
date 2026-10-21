#!/usr/bin/env python3
"""Install the Holon adapter into Operant (idempotent; everything lives in its own HERMES_HOME).

    python3 adapters/operant/install.py --holon-home ~/holon --base-url <gateway-url>/v1 \\
        --model <model> --api-key-file ~/.config/holon/model.key [--operant /path/to/operant] [--dry-run]

1. creates HERMES_HOME=$HOLON_HOME/adapters/operant/home, so Holon's employees never share the
   user's own operant config, sessions, memory or approval allowlist (operant honors HERMES_HOME)
2. writes the two posture configs the driver switches between (the {posture} placeholder):
     observe.toml  every stage but execute: write, mutation and notification tools disabled, so
                   an observer can read, search and think but change nothing; the kernel's
                   working-directory fingerprint is the read-only check (read_only_enforced=false)
     act.toml      the execute stage: the same core config plus command_allowlist = ["*"], so an
                   approved plan never stalls on a permission prompt an unattended harness cannot
                   answer (hermes --yolo parity; the hardline command floor still applies)
    Both disable operant's own org/cron surfaces ([bylaws], [context_injection], [socialization]
    and the [kernel] continual-harness lane) and keep [memory] on inside the isolated home: the
    body's own working memory, while the organization's memory is Holon's (boundary doc S1/S3).
    Operant's only memory engine today is memory-wire, so there is no provider pin to write;
    provider-level isolation is the runtime's job (its R-08), not a config the adapter can set.
3. mounts the Holon MCP server (stdio, one process per run, so each run's token stays its own)
   in both postures — UNLESS the pinned operant binary registers the holon_* native tools under
   the run env (probed at install; operant iter-854+): those builds get no MCP mount, the native
   tools are the organization's read/submit surface. Observe stages hand their result in with
   holon_submit, since they cannot write result.json. The run token reaches the tools through
   the environment operant's child inherits.
4. exports Holon's skills to $HERMES_HOME/skills (registered: verified skill changes refresh them)
5. installs the driver, pinned to this operant binary and HERMES_HOME
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE.parent))
import install_lib  # noqa: E402  (the shared installer seam: R3 of the 2026-10-20 bridge plan)

# G-e: the observe posture's allowlist comes from the kernel, not from a copy here. Two lists that
# must agree are one list or they drift, and this one is the kernel's own statement of what an
# observer may run (it is the same list `policy.json` carries).
sys.path.insert(0, str(REPO))
from holon.execution.policy import READ_ONLY_COMMANDS  # noqa: E402
# `file_write` is the one write tool an observer keeps: it is how the body hands in its result, and
# the fence is the kernel's working-directory guard, not the tool list. `aft_bash` is the same case
# one level down — it is the harness's shell, the only way an observer can run `ls` or `cat` to see
# what it plans, and the run's `policy.json` plus the kernel's workdir fingerprint are what stop a
# mutation, not the presence of the tool.
OBSERVE_TOOLS = (*READ_ONLY_COMMANDS, "aft_bash", "aft_read", "file_write", "file_list", "file_search")

# operant tool names (crates/operant-core/src/tools/builtin.rs) that mutate the machine, the
# organization's records, or reach a human. An observer gets none of them.
WRITE_TOOLS = [
    "file_write", "patch", "terminal", "process", "code_execution", "checkpoint", "working_diff",
    "skill_manage", "learning_mutation", "learning_refine", "config_manage", "cron", "kanban",
    "image_generate", "notification", "send_message", "react_to_message", "discord",
    "discord_admin", "feishu_doc", "feishu_drive", "home_assistant", "spotify_playback",
    "spotify_devices", "spotify_queue", "spotify_search", "spotify_playlists", "spotify_albums",
    "spotify_library", "tts", "neutts_synthesize", "approval_request",
]
# AFT bridge tools (IDE-grade, auto-downloaded): keep the read/analyze ones, deny the writers.
AFT_WRITE = ["aft_edit", "aft_write", "aft_patch", "aft_append"]


def config_text(base_url: str, model: str, holon_cmd: str, skills_dir: Path,
                memory_dir: Path, observe: bool, api_key: str | None,
                native_tools: bool = False) -> str:
    tools = sorted(WRITE_TOOLS + AFT_WRITE) if observe else AFT_WRITE
    # G-a (measured 2026-10-21): `file_write` is a write tool, and it is also the only way a body
    # hands in a result when the submit tool is not reachable. Denying it in observe leaves the
    # body with nothing to hand in — the fence is the kernel's own working-directory guard, which
    # snapshots the workdir and excludes the run directory, not the tool list. So `file_write`
    # stays available in observe; everything that mutates the machine, the organization's records,
    # or a human is still denied.
    if "file_write" in tools:
        tools = sorted(set(tools) - {"file_write"})
    lines = [
        "# Written by Holon's operant adapter installer. One posture config per {posture} run.",
        f"# Posture: {'observe — no write/mutation/notification tools' if observe else 'act — the plan was approved'}.",
        "# Regenerate by re-running install.py.",
        "",
    ]
    if not observe:
        lines += [
            "# execute posture: the plan was approved; an unattended run cannot answer a",
            "# permission prompt, so every tool is allowlisted (a TOP-LEVEL key: it seeds the",
            "# agent's approval allowlist and matches tool names or globs). The smart-approval",
            "# hardline floor (destructive command patterns) still applies.",
            'command_allowlist = ["*"]',
            "",
        ]
    else:
        # G-e (measured 2026-10-21): lockdown mode refuses a tool that has "neither an allow entry
        # nor a standing grant", and the observe posture set none — so a read-only `aft_bash` probe
        # and the observer's own `file_write` hand-in were both escalated and recorded as refusals,
        # which the kernel then treats as blockers. lockdown is the right default; what it needs is
        # an entry, not a relaxation.
        #
        # So the observe posture names exactly what an observer may run: the kernel's read-only
        # command vocabulary (holon/execution/policy.py, the same list policy.json carries) and
        # `file_write`, the tool that writes this run's own result. The kernel compiles the code, so
        # the two lists cannot drift; the harness's own allowlist is what lets it through.
        lines += [
            "# observe posture: lockdown mode refuses a tool with no allow entry, so the observer's",
            "# read-only probes and its own result hand-in both need one. The list is the kernel's",
            "# read-only command vocabulary plus `file_write` (the run's result.json) — the same list",
            "# policy.json carries. Read-only by construction, like the kernel's own posture.",
            'command_allowlist = [' + ", ".join(json.dumps(c) for c in OBSERVE_TOOLS) + ']',
            "",
        ]
    lines += [
        "[client]",
        f'base_url = "{base_url}"',
        "timeout_secs = 600",
        "max_context_length = 128000",
    ]
    if api_key:
        lines.append(f'api_key = "{api_key}"')
    lines += [
        "",
        "[agent]",
        f'model = "{model}"',
        "max_iterations = 40",
        "stream = false",
        "context_window = 128000",
        "show_reasoning = false",
        # free-route reality (G1 live day): a stage body's turn on a
        # free route under pressure can exceed 300s (three live bodies
        # died on the ceiling: 120s twice, then 300s); the loop's
        # per-request ceiling must match the client or the body dies at
        # the default. The kernel's own hermes driver allowed 3600s
        # wall-clock — 600s per request is conservative for a body.
        "request_timeout_secs = 600",
        # same live day: the 30s default killed aft_read twice (the AFT
        # bridge cold-start downloads its binary); a body's tool calls
        # (AFT, web fetches) legitimately run longer than interactive use
        "tool_timeout_secs = 120",
        "",
        "[tui]",
        "rich_output = false",
        "",
        "# The organization's records are Holon's (boundary doc S1/S3): operant's own org layer,",
        "# DM context injection and socialization are off for every Holon run; the cron/kanban",
        "# TOOLS are disabled through tools.disabled_tools (they are tools, not config sections).",
        '[bylaws]',
        'unrestricted_default = "lockdown"',
        'unattended_posture = "lockdown"',
        "[context_injection]",
        "enabled = false",
        "[socialization]",
        "enabled = false",
        "",
        "# The continual-harness lane is off in a Holon run: the organization's scheduling and",
        "# supervision are Holon's, not the body's. KernelSettings is a field of",
        "# [tools] (config.rs: ToolSettings.kernel), so the sub-table is [tools.kernel] — a",
        "# top-level [kernel] is an unknown field and operant refuses the whole config",
        "# (deny_unknown_fields; the live day's sixth body died on it).",
        "[tools.kernel]",
        "enabled = false",
        "",
        "# The body's own working memory, inside this isolated home. Operant's only memory engine",
        "# today is memory-wire — there is no provider pin to write (R-08/R-10); organization-level",
        "# memory is Holon's, reached through holon_memory_search (boundary doc S1/S3).",
        "# KB-8: the [skills].memory_dir below is the fallback store; at spawn time the kernel's",
        "# driver env sets OPERANT_MEMORY_DIR to the run's own memory/ directory, so each run's",
        "# body works in its own bank — one run's session memory never bleeds into another's.",
        "[memory]",
        "enabled = true",
        "",
        "[skills]",
        f'root_dir = "{skills_dir}"',
        f'memory_dir = "{memory_dir}"',
        "autoload = true",
        "",
        "[mcp]",
        "autoload = true",
        "deferred_loading = true",
    ]
    # W1/O-03: a binary that registers the holon_* native tools under the
    # run env does not need the resident Python MCP server — the native
    # tools are the replacement, and no Python process stays alive per run.
    # An older (or probed-unknown) binary keeps the mount: it has no other
    # path to the organization.
    if not native_tools:
        lines += [
            "",
            "[[mcp.servers]]",
            'name = "holon"',
            'transport = "stdio"',
            f'command = "{holon_cmd}"',
            'args = ["mcp"]',
            "enabled = true",
        ]
    lines += [
        "",
        "[tools]",
        # the registry's default tool timeout is [tools] registry_timeout_secs
        # (default 30) — NOT [agent] tool_timeout_secs; a cold-starting AFT
        # bridge (binary download) blew the 30s default twice on the live day
        "registry_timeout_secs = 120",
        f"disabled_tools = {json.dumps(tools)}",
    ]
    if not observe:
        pass  # command_allowlist is emitted in the file header, before any [section]
    return "\n".join(lines) + "\n"


def carries_native_tools(operant: str) -> bool:
    """Whether this operant binary registers the holon_* native tools under a
    Holon run env (registration keys on HOLON_RUN_DIR — operant iter-854).
    Such builds make the resident `holon mcp` Python subprocess redundant
    (W1/O-03's second half): one probe at install time decides the mount."""
    try:
        p = subprocess.run([operant, "tools", "list"], capture_output=True, text=True,
                           env={**os.environ, "HOLON_RUN_DIR": "/probe"}, timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return "holon_" in (p.stdout or "")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--holon-home", default=os.environ.get("HOLON_HOME", str(Path("~/holon").expanduser())))
    ap.add_argument("--operant", help="the operant binary (default: the first on PATH); its path is pinned")
    ap.add_argument("--base-url", required=True, help="OpenAI-compatible endpoint for Holon's employees")
    ap.add_argument("--model", required=True)
    ap.add_argument("--api-key-file", help="file holding the endpoint's key (omit for an unauthenticated endpoint)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    home = Path(a.holon_home).expanduser().resolve()
    ohome = home / "adapters" / "operant" / "home"
    # R3: the pin is taken through the seam, which refuses a binary that cannot start.
    # The live instance's driver named a stale root-owned `operant` built against a
    # different ONNX Runtime than this repo pins, so every run died in the loader and
    # the error text pointed at the library — which was correct. The check that would
    # have caught it: the binary must pass its own `--version` before it is pinned.
    try:
        operant = install_lib.pin_binary("operant", a.operant, required=not a.dry_run, check=not a.dry_run)
    except install_lib.InstallError as e:
        print(f"operant: {e}", file=sys.stderr)
        return 2
    key = Path(a.api_key_file).expanduser().read_text().strip() if a.api_key_file else None
    native = not a.dry_run and carries_native_tools(operant)

    driver = (HERE / "driver.toml").read_text().replace("{operant}", operant).replace("{ohome}", str(ohome))
    tomllib.loads(driver)
    skills_dir = ohome / "skills"
    holon_cmd = REPO / "bin" / "holon"
    obs = config_text(a.base_url, a.model, str(holon_cmd), skills_dir, ohome / "memory", True, key, native)
    act = config_text(a.base_url, a.model, str(holon_cmd), skills_dir, ohome / "memory", False, key, native)
    tomllib.loads(obs)
    tomllib.loads(act)

    print(f"operant: {operant}\nHERMES_HOME: {ohome}\nprovider: {a.base_url} ({a.model})")
    print("native holon tools: " + ("yes — the resident holon mcp mount is dropped" if native else "no (dry-run probe or older build) — the resident holon mcp server stays mounted"))
    if a.dry_run:
        shown = driver + "--- observe.toml ---\n" + obs + "--- act.toml ---\n" + act
        if key:
            shown = shown.replace(f'api_key = "{key}"', 'api_key = "<redacted>"')  # a dry run never prints keys
        print("dry run: no changes written\n" + shown)
        return 0

    ohome.mkdir(parents=True, exist_ok=True)
    for name, text in (("observe.toml", obs), ("act.toml", act)):
        f = ohome / name
        f.write_text(text)
        if key:
            f.chmod(0o600)  # the key rides the config, so the config is private
    try:
        install_lib.export_skills(home, skills_dir)
    except install_lib.InstallError as e:
        print(f"skills: export FAILED — {e}", file=sys.stderr)
        return 1
    print("skills: exported")
    path = install_lib.write_driver(home, "operant", driver)
    print(f"driver: {path}")
    print(f"verify: {install_lib.verify(home, operant)}")
    print("next: `holon adapter test operant` runs the conformance suite against this harness")
    return 0


if __name__ == "__main__":
    sys.exit(main())
