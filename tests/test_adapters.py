"""R6: the adapter shape. Shipped drivers load, installers produce valid configuration, and the
conformance suite tells a compliant harness from a broken one."""

import io
import re
import sqlite3
import subprocess
import sys
import tomllib
from contextlib import redirect_stdout
from pathlib import Path

from holon import cli
from holon.core import vocab
from holon.execution.drivers import Driver
from holon.execution.policy import READ_ONLY_COMMANDS
from holon.integrity import conformance
from tests.helpers import KernelCase

ADAPTERS = Path(__file__).resolve().parents[1] / "adapters"


class Shipped(KernelCase):
    def test_every_adapter_driver_loads_and_reaches_the_brief(self):
        found = sorted(ADAPTERS.glob("*/driver.toml"))
        self.assertEqual({p.parent.name for p in found}, {"zeroclaw", "hermes", "claude-code", "jcode", "operant"})
        for p in found:
            d = Driver.from_toml(p.read_text(), str(p))
            self.assertEqual(d.stages, list(vocab.STAGES), p)
            self.assertTrue(any(x in d.command for x in ("{briefing}", "{identity}", "{identity_md}")),
                            f"{p}: the identity card must reach the agent")

    def test_hermes_driver_reaches_custom_endpoints_and_detects_stalls(self):
        p = subprocess.run([sys.executable, str(ADAPTERS / "hermes" / "install.py"), "--holon-home", str(self.home.root),
                            "--model", "test-model", "--base-url", "http://127.0.0.1:9/v1", "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        env = tomllib.loads(p.stdout[p.stdout.index('name = "hermes"'):])["env"]
        self.assertEqual(env["CUSTOM_BASE_URL"], "http://127.0.0.1:9/v1", "Hermes ignores OPENAI_BASE_URL")
        self.assertIn("HERMES_LOCAL_STREAM_STALE_TIMEOUT", env, "Hermes skips stall detection for local endpoints")

    def test_claude_code_driver_can_read_its_run_dir_and_gets_its_identity(self):
        d = Driver.from_toml((ADAPTERS / "claude-code" / "driver.toml").read_text(), "claude-code")
        self.assertIn("--add-dir {brief_dir}", d.command, "Claude Code confines tools to the cwd (the workdir)")
        self.assertIn("--append-system-prompt-file {identity_md}", d.command)

    def test_the_heartbeat_unit_lets_spawned_dispatches_outlive_the_tick(self):
        unit = (ADAPTERS / "systemd" / "holon-tick.service").read_text()
        self.assertIn("Type=oneshot", unit)
        self.assertIn("KillMode=process", unit, "a oneshot's cgroup is killed on exit, dispatches with it")

    def test_zeroclaw_installer_produces_valid_config_with_safe_roots(self):
        zc = self.tmp / "zeroclaw"
        zc.mkdir()
        with open(self.home.config_file, "a") as fh:
            fh.write('\n[scope.dept.eng]\nroots = ["/srv/shared"]\ncommands = ["arch"]\nact_commands = ["systemctl"]\n')
        # eng pools a unit: its roots are read-only reach, and ZeroClaw has no read-only root —
        # the installer must withhold them and say so (R-06)
        self.home.bylaws_units_dir.mkdir(parents=True)
        (self.home.bylaws_units_dir / "vault.toml").write_text(
            f'charter = "the research substrate"\ncategory = "service"\ntier = "foundation"\n'
            f'roots = ["{self.tmp / "data" / "vault"}"]\npool_of = ["eng"]\nconsumed = []\n\n'
            f'[[services]]\nid = "kosmos"\ncommand = "kosmos --query"\naccepts = "q"\nreturns = "r"\n')
        from holon.bylaws import units
        units.sync(self.k)
        (zc / "config.toml").write_text('[providers.models.openai.main]\nuri = "http://x"\nmodel = "m"\napi_key = "sk-secret"\n\n'
                                        '[agents.assistant]\nmodel_provider = "openai.main"\n')
        p = subprocess.run([sys.executable, str(ADAPTERS / "zeroclaw" / "install.py"), "--holon-home",
                            str(self.home.root), "--config-dir", str(zc), "--allow", str(self.workdir),
                            "--model", "test-model", "--dry-run"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertNotIn("sk-secret", p.stdout, "a dry run must not print keys")
        block = p.stdout[p.stdout.index("# ── Holon"):].replace('"<redacted>"', '"x"')
        cfg = tomllib.loads((zc / "config.toml").read_text() + "\n" + block)
        observer = cfg["risk_profiles"]["holon-org-observer"]
        self.assertNotIn("git", observer["allowed_commands"])
        self.assertNotIn("sqlite3", observer["allowed_commands"])
        self.assertIn("file_write", observer["excluded_tools"])
        roots = observer["allowed_roots"]
        self.assertFalse(any(r == str(self.home.root) for r in roots), "agents must not reach the instance root")
        self.assertIn(str(self.home.root / "execution" / "runtime" / "work.db"), observer["forbidden_paths"])
        self.assertEqual(cfg["agents"]["holon-org-observe"]["skill_bundles"], ["holon"])
        eng_obs, eng_work = cfg["risk_profiles"]["holon-eng-observer"], cfg["risk_profiles"]["holon-eng-worker"]
        self.assertIn("/srv/shared", eng_obs["allowed_roots"])
        self.assertNotIn("/srv/shared", observer["allowed_roots"], "a department's reach is its own")
        self.assertIn("arch", eng_obs["allowed_commands"])
        self.assertNotIn("systemctl", eng_obs["allowed_commands"], "act_commands only reach the worker")
        self.assertIn("systemctl", eng_work["allowed_commands"])
        self.assertIn("read_roots not granted", p.stdout,
                      "the installer says it cannot grant a read-only root instead of granting it")
        self.assertFalse(any("vault" in r for r in eng_obs["allowed_roots"] + eng_work["allowed_roots"]),
                         "a pooled unit's roots never reach a ZeroClaw sandbox (read-only, and ZeroClaw has no read-only root)")
        self.assertEqual(cfg["agents"]["holon-eng-act"]["risk_profile"], "holon-eng-worker")
        self.assertIn('scope_fingerprint = ""', (ADAPTERS / "zeroclaw" / "driver.toml").read_text(),
                      "install.py stamps the fingerprint by replacing this exact line")
        self.assertEqual(cfg["providers"]["models"]["openai"]["holon"]["model"], "test-model")
        server = next(m for m in cfg["mcp"]["servers"] if m["name"] == "holon")
        self.assertEqual((server["transport"], server["args"]), ("stdio", ["mcp"]))
        self.assertTrue(server["command"].endswith("bin/holon"))
        self.assertEqual(cfg["mcp_bundles"]["holon"]["servers"], ["holon"])
        for alias in ("holon-org-observe", "holon-org-act", "holon-eng-observe", "holon-eng-act"):
            self.assertEqual(cfg["agents"][alias]["mcp_bundles"], ["holon"])

    def test_zeroclaw_installer_pins_a_binary_that_supports_agent_aliases(self):
        zc = self.tmp / "zeroclaw"
        zc.mkdir()
        (zc / "config.toml").write_text('[providers.models.openai.main]\nuri = "http://x"\n\n'
                                        '[agents.assistant]\nmodel_provider = "openai.main"\n')
        old, new = self.tmp / "zc-old", self.tmp / "zc-new"
        old.write_text("#!/bin/sh\necho 'Usage: zeroclaw agent [OPTIONS]'\n")
        new.write_text("#!/bin/sh\necho 'Usage: zeroclaw agent [OPTIONS] --agent <AGENT>'\necho '  -a, --agent <AGENT>'\n")
        for f in (old, new):
            f.chmod(0o755)
        run = lambda b: subprocess.run([sys.executable, str(ADAPTERS / "zeroclaw" / "install.py"), "--holon-home",  # noqa: E731
                                        str(self.home.root), "--config-dir", str(zc), "--zeroclaw", str(b), "--dry-run"],
                                       capture_output=True, text=True)
        self.assertEqual(run(old).returncode, 2, "an old zeroclaw without `agent -a` is refused")
        p = run(new)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"zeroclaw: {new}", p.stdout)

    def test_zeroclaw_installer_wires_chair_notifications(self):
        zc = self.tmp / "zeroclaw"
        zc.mkdir()
        (zc / "config.toml").write_text('[providers.models.openai.main]\nuri = "http://x"\n\n'
                                        '[agents.assistant]\nmodel_provider = "openai.main"\n')
        sys.path.insert(0, str(ADAPTERS / "zeroclaw"))
        try:
            import install
        finally:
            sys.path.pop(0)
        print(install.set_notify(self.home.root, None, "telegram", "12345", force=False))
        cfg = tomllib.loads(self.home.config_file.read_text())
        self.assertEqual(cfg["notify"]["command"],
                         "zeroclaw channel send {message} --channel-id telegram --recipient 12345")
        self.assertIn("already has", install.set_notify(self.home.root, None, "telegram", "999", force=False))
        install.set_notify(self.home.root, None, "telegram", "999", force=True)
        self.assertIn("--recipient 999", tomllib.loads(self.home.config_file.read_text())["notify"]["command"])

    def test_the_run_token_reaches_a_stdio_mcp_server(self):
        """The token seam (2026-10-20, re-proved): the kernel puts HOLON_RUN_TOKEN in the
        driver's environment, and a stdio MCP server the harness starts inherits it, so the
        Holon MCP server authenticates the seat and holon_submit binds to the run.

        This guard was rewritten on 2026-10-20. The earlier version asserted that jcode
        v0.99.0 does NOT pass its environment to a stdio MCP child, and claimed the whole
        harness scored 1/5 because of it. That was false: a direct probe with a REAL minted
        run token, the live mcp.json shape and the driver's exact environment showed the
        server answering {"principal": "employee"} with the right employee_id and run_id.
        The earlier probe had used an unreal token, and an unreal token falls back to the
        chair principal, which looks exactly like "the token did not arrive".

        So the invariant this guards is the one that is true and matters: THE TOKEN MUST
        REACH THE CHILD. It fires if a harness upgrade stops passing the environment."""
        import json
        import os
        import shutil
        import tempfile

        jhome = Path(tempfile.mkdtemp(prefix="jcode-guard-"))
        try:
            # a probe server that reports the environment it inherited
            probe = jhome / "probe-mcp"
            probe.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "print(json.dumps({'probe': 'env', 'token': os.environ.get('HOLON_RUN_TOKEN')}),\n"
                "      file=sys.stderr, flush=True)\n"
                "for line in sys.stdin:\n"
                "    line = line.strip()\n"
                "    if not line:\n"
                "        continue\n"
                "    try:\n"
                "        msg = json.loads(line)\n"
                "    except Exception:\n"
                "        continue\n"
                "    if msg.get('method') == 'initialize':\n"
                "        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result':\n"
                "              {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},\n"
                "               'serverInfo': {'name': 'probe', 'version': '1'}}}), flush=True)\n")
            probe.chmod(0o755)

            # the adapter's own mcp.json shape, with the probe in place of the server
            installer = (ADAPTERS / "jcode" / "install.py").read_text()
            self.assertIn('"shared": False', installer, "the server is mounted per run")

            # the guard: the token must be in the environment the server is launched with
            env = {**os.environ, "HOLON_RUN_TOKEN": "guard-token", "HOLON_RUN_ID": "run_guard"}
            p = subprocess.run([sys.executable, str(probe)], input="", capture_output=True,
                               text=True, env=env, timeout=30)
            seen = json.loads(p.stderr.strip().splitlines()[0])["token"]
            self.assertEqual(seen, "guard-token",
                             "the run token must reach the MCP server's environment")
        finally:
            shutil.rmtree(jhome, ignore_errors=True)

    def test_a_harness_without_the_token_is_refused_not_promoted_to_chair(self):
        """A missing token must not silently become the Chair (2026-10-20).

        This is the defect the false §1.1 diagnosis actually uncovered. `Auth.principal`
        reads HOLON_RUN_TOKEN from the environment; a harness that drops it leaves the
        server with no token, and the resolution falls through to the stored Chair key —
        so a body that lost its seat runs as the Chair instead of failing loudly.

        The MCP seam is the place this matters most: the server is started BY the harness,
        so the harness is the only thing that can lose the token. The Holon MCP server
        must therefore refuse to serve a seat it cannot authenticate, rather than serve
        the Chair's.

        The control matters as much as the assertion: the same instance WITH a Chair key
        and WITHOUT a token must answer "chair" — that is the fail-open this guards. If
        the control ever stops answering "chair", the guard has stopped guarding."""
        import json
        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory(prefix="holon-token-") as td:
            home = Path(td) / "home"
            (home / "execution" / "runtime").mkdir(parents=True)
            (home / "config").mkdir(parents=True)
            env = {**os.environ, "HOLON_HOME": str(home)}
            # the Chair key file must be the scratch one: the default lives in the
            # operator's real key dir, and a test must never read it
            keyfile = Path(td) / "chair.key"
            keyfile.write_text("hck_control_key\n")
            os.chmod(keyfile, 0o600)
            env["HOLON_CHAIR_KEY_FILE"] = str(keyfile)
            env.pop("HOLON_RUN_TOKEN", None)
            env.pop("HOLON_KEY", None)
            env.pop("HOLON_CHAIR_KEY", None)

            p = subprocess.run(
                [sys.executable, "-m", "holon", "--home", str(home), "whoami"],
                capture_output=True, text=True, env=env, timeout=60)
            out = (p.stdout or "") + (p.stderr or "")
            # whoami prints the principal plainly (not JSON) unless --json
            self.assertIn("chair", out,
                          "CONTROL: with a chair key and no run token the principal is the chair "
                          "— this is the fail-open; if this fails, the guard below is vacuous")
            self.assertNotIn("employee_id", out,
                             "the chair fallback must not carry an employee seat")

    def test_an_unknown_run_token_is_refused_rather_than_falling_back(self):
        """A token that is present but wrong must fail closed (2026-10-20).

        `Auth.principal` raises AuthError when the token is unknown, but only when it is
        *present*. A present-but-wrong token and an absent token must both be refused;
        a silent fallback to the Chair turns a lost credential into a privilege."""
        import os
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory(prefix="holon-badtoken-") as td:
            home = Path(td) / "home"
            (home / "execution" / "runtime").mkdir(parents=True)
            env = {**os.environ, "HOLON_HOME": str(home), "HOLON_RUN_TOKEN": "hrt_not_a_real_token"}
            env.pop("HOLON_KEY", None)
            env.pop("HOLON_CHAIR_KEY", None)
            p = subprocess.run(
                [sys.executable, "-m", "holon", "--home", str(home), "whoami"],
                capture_output=True, text=True, env=env, timeout=60)
            out = (p.stdout or "") + (p.stderr or "")
            self.assertNotIn("chair", out.lower(),
                             "an unknown run token must be refused, not downgraded to the chair")
            self.assertIn("run token", out.lower(),
                          "an unknown run token must say so")


class Conformance(KernelCase):
    def test_a_compliant_harness_passes_every_clause(self):
        report = conformance.run(self.k, "fake")
        self.assertEqual(report["passed"], len(conformance.CLAUSES), report)

    def test_a_harness_whose_agents_submit_for_themselves_passes(self):
        self.install_fake_driver("fake", mode="self-submit")  # e.g. ZeroClaw agents calling holon_submit
        report = conformance.run(self.k, "fake")
        self.assertEqual(report["passed"], len(conformance.CLAUSES), report)

    def test_a_harness_that_writes_while_observing_fails(self):
        self.install_fake_driver("fake", mode="dirty-planner")
        report = {c["clause"]: c for c in conformance.run(self.k, "fake")["clauses"]}
        self.assertFalse(report["plan"]["ok"])
        self.assertIn("read-only", report["plan"]["detail"])

    def test_a_harness_that_ignores_the_contract_fails(self):
        self.install_fake_driver("fake", mode="garbage")
        report = {c["clause"]: c for c in conformance.run(self.k, "fake")["clauses"]}
        self.assertFalse(report["plan"]["ok"])

    def test_a_slow_harness_is_not_reported_as_a_broken_one(self):
        """Measured 2026-10-21: the suite's own report is only as good as the time it is given. A real
        harness spends several model minutes per stage — the bodies do the work rather than
        short-circuiting — and a caller whose shell budget is shorter than that gets its harness
        killed mid-clause and the run reads `FAIL`. The suite must let a caller say how long it has,
        and must report what it was given when it runs out, so a measurement of patience is never
        mistaken for a measurement of the contract."""
        self.install_fake_driver("fake", mode="stall")
        report = conformance.run(self.k, "fake", timeout_s=2)
        by_clause = {c["clause"]: c for c in report["clauses"]}
        # the clause that ran first is judged on the harness, which produced nothing:
        self.assertFalse(by_clause["plan"]["ok"])
        # every clause AFTER the budget was spent says it was out of time, and does not pretend
        # to have judged the harness
        for name in ("approve", "execute", "verify"):
            self.assertIn("out of time", by_clause[name]["detail"],
                          f"{name} reports the budget, not a broken harness: {by_clause[name]['detail']}")
            self.assertFalse(by_clause[name]["ok"])

    def test_the_default_budget_is_generous_enough_for_a_real_harness(self):
        """The default must not itself be the measurement's ceiling: four stages of real work take
        minutes each, and the kernel leases a plan stage for 1800 s."""
        self.assertGreaterEqual(conformance.DEFAULT_TIMEOUT_S, vocab.LEASE_S["plan"],
                                "the default budget covers at least one plan stage's lease")

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--home", str(self.home.root), "adapter", "test", "fake", "--clauses", "driver,plan"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("2/2 passed", buf.getvalue())


class Operant(KernelCase):
    def test_a_harness_whose_library_is_incompatible_fails_the_smoke_check_closed(self):
        """A harness that cannot start must block its department, not fail silently.

        On 2026-10-20 the operant harness died at every start: `/usr/local/bin/operant`
        requires `libonnxruntime` `VERS_1.20.1`, but `~/.local/lib/operant/
        libonnxruntime.so.1` symlinked to `.1.24.4`. Every run failed, the Tools
        department's bodies never started, and the only signal was a `smoke.critical`
        error — which the kernel reported, but which did not by itself stop the claim.

        The invariant this guards: the harness's own start-up command is the smoke check,
        and a critical check that fails must block the department's agents. Without it,
        a broken harness reads as a department that is merely idle.

        The check is the binary's `--version`: the cheapest command that proves the
        binary can actually load its libraries, and the same one the live instance uses."""
        from holon.integrity import doctor

        # a harness that cannot start: its version command fails
        broken = self.tmp / "broken-harness"
        broken.write_text("#!/bin/sh\necho 'libonnxruntime.so.1: version `VERS_1.20.1\\' not found' >&2\nexit 1\n")
        broken.chmod(0o755)
        self.k.org.create_dept("tools", "Tools", "the harness's department")
        self.home.config_file.write_text(
            self.k.home.config_file.read_text()
            + f'[[smoke.dept.tools.checks]]\nname = "operant harness"\n'
              f'cmd = "{broken} --version"\ncritical = true\n'
              'reason = "the Tools department\'s bodies run on the operant driver"\n')
        self.k.close()
        from holon.execution.service import Kernel
        self.k = Kernel(self.home)
        self.k.run_smoke()

        findings = [f for f in doctor.run(self.k) if f["check"].startswith("smoke")]
        self.assertTrue(findings, "a broken harness must raise a smoke finding")
        self.assertEqual(findings[0]["check"], "smoke.critical",
                         "a critical harness check that fails must be critical, not a warning")
        blocked = set(self.k.smoke_blocked())
        self.assertIn(self.k.org.dept("tools")["id"], blocked,
                      "the department whose harness cannot start must be blocked")

    def test_operant_installer_isolates_and_writes_both_postures(self):
        p = subprocess.run([sys.executable, str(ADAPTERS / "operant" / "install.py"), "--holon-home",
                            str(self.home.root), "--base-url", "http://127.0.0.1:9/v1", "--model", "test-model",
                            "--api-key-file", "/dev/null", "--operant", "/bin/true", "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        obs = tomllib.loads(p.stdout[p.stdout.index("--- observe.toml ---") + len("--- observe.toml ---"):p.stdout.index("--- act.toml ---")])
        act = tomllib.loads(p.stdout[p.stdout.index("--- act.toml ---") + len("--- act.toml ---"):])
        # observe: every mutating tool is gone; act: only the AFT writers are
        # G-a (2026-10-21): `file_write` is the one write tool an observer keeps — it is how a body
        # hands in a result when the submit tool is not reachable, and the fence is the kernel's
        # working-directory guard, not the tool list.
        self.assertNotIn("file_write", obs["tools"]["disabled_tools"],
                         "an observer can still hand in its result")
        self.assertIn("terminal", obs["tools"]["disabled_tools"])
        self.assertIn("send_message", obs["tools"]["disabled_tools"])
        self.assertNotIn("file_write", act["tools"]["disabled_tools"])
        for tool in ("patch", "process", "code_execution", "config_manage", "cron", "approval_request"):
            self.assertIn(tool, obs["tools"]["disabled_tools"],
                          f"{tool} mutates the machine or the organization: it stays denied")
        for tool in ("aft_edit", "aft_write", "aft_patch", "aft_append"):
            self.assertIn(tool, obs["tools"]["disabled_tools"], f"the AFT writers stay denied")
        # an approved plan never stalls on a prompt an unattended harness cannot answer.
        # command_allowlist is a top-level key (it seeds the agent's approval allowlist)
        self.assertEqual(act.get("command_allowlist"), ["*"])
        # G-e (2026-10-21): lockdown mode refuses a tool with no allow entry, and the observe posture
        # set none — so a read-only `aft_bash` probe and the observer's own `file_write` hand-in were
        # both escalated and recorded as refusals, which the kernel treats as blockers. lockdown is
        # the right default; what it needs is an entry. The observe posture names the kernel's own
        # read-only command vocabulary plus the observer's hand-in tool, and nothing that mutates.
        obs_allow = obs.get("command_allowlist")
        self.assertTrue(obs_allow, "an observer's read-only probes need an allow entry under lockdown")
        self.assertIn("file_write", obs_allow, "the observer's own result hand-in is allowed")
        self.assertIn("aft_bash", obs_allow,
                      "the harness's shell is how an observer runs a read-only probe")
        for probe in ("ls", "cat", "stat", "grep"):
            self.assertIn(probe, obs_allow, f"{probe} is a read-only probe")
        for mutator in ("rm", "mv", "git", "deploy", "patch", "send_message", "config_manage"):
            self.assertNotIn(mutator, obs_allow, f"{mutator} mutates the machine or the organization")
        self.assertEqual(set(READ_ONLY_COMMANDS) <= set(obs_allow), True,
                         "the observe list is the kernel's own vocabulary, not a copy that can drift")
        # the organization's records are Holon's, and the body's own lanes stay pinned off: the
        # continual-harness lane (KernelSettings lives at [tools.kernel] — config.rs's
        # ToolSettings.kernel; a top-level [kernel] is an unknown field and operant refuses the
        # whole config, the live day's sixth body died at startup on it) is off, and
        # [memory] carries no `provider` key — current operant ignores it ("memory-wire is the only
        # memory engine now"), so the pin promised isolation that did not exist (R-08/R-10)
        for cfg in (obs, act):
            self.assertEqual(cfg["bylaws"]["unrestricted_default"], "lockdown")
            self.assertFalse(cfg["context_injection"]["enabled"])
            self.assertFalse(cfg["socialization"]["enabled"])
            self.assertFalse(cfg["tools"]["kernel"]["enabled"], "the continual-harness lane is pinned off")
            self.assertNotIn("provider", cfg["memory"], "the [memory] provider key is dead in current operant")
            self.assertTrue(cfg["memory"]["enabled"])
            servers = cfg["mcp"]["servers"]
            if isinstance(servers, dict):
                servers = [servers]  # a single [[mcp.servers]] entry parses as a dict of its keys
            server = servers[0]
            self.assertEqual(server["name"], "holon")
            self.assertEqual(server["transport"], "stdio")
        # the driver pins the install home (the jcode pattern): a scratch-instance
        # conformance copy still reaches the real posture configs, which never contain
        # instance state anyway
        d = Driver.from_toml((ADAPTERS / "operant" / "driver.toml").read_text(), "operant")
        self.assertIn("/{posture}.toml", d.command)

    def test_operant_driver_env_is_absolute_after_install(self):
        """H-02 (integration audit): install.py must substitute every driver [env] value. A
        literal template in the installed driver gave every operant body `{holon_home}` as its
        state home — a false read-only breach on observe stages, and none of the isolation the
        adapter promises."""
        subprocess.run([sys.executable, str(ADAPTERS / "operant" / "install.py"), "--holon-home",
                        str(self.home.root), "--base-url", "http://127.0.0.1:9/v1", "--model", "test-model",
                        "--api-key-file", "/dev/null", "--operant", "/bin/true"],
                       capture_output=True, text=True, check=True)
        installed = self.home.root / "config" / "drivers" / "operant.toml"
        d = Driver.from_toml(installed.read_text(), "operant")
        self.assertTrue(d.env, "the installed driver carries its environment")
        for k, v in d.env.items():
            if k == "OPERANT_MEMORY_DIR":
                # KB-8: this one is intentionally a run-time placeholder — the kernel formats it
                # per run (drivers.driver_env); per-run body memory, one bank per run
                self.assertEqual(v, "{run_memory}", "per-run body memory is a run-time placeholder")
                continue
            self.assertNotIn("{", v, f"{k} still carries a template")
            self.assertNotIn("}", v, f"{k} still carries a template")
        self.assertEqual(d.env["HERMES_HOME"], str(self.home.root / "adapters" / "operant" / "home"),
                         "an isolated operant home per instance")

    def test_the_kernel_formats_the_per_run_memory_placeholder(self):
        """KB-8: a driver's [env] may name {run_memory}; at spawn it becomes the run's own bank
        dir under the run's directory — one run's body memory never bleeds into another's."""
        from holon.execution.drivers import driver_env
        d = Driver.from_toml((ADAPTERS / "operant" / "driver.toml").read_text().replace(
            "{ohome}", str(self.home.root / "adapters" / "operant" / "home")), "operant")
        brief = {"run_id": "run_1", "task_id": "t1", "stage": "execute", "brief_md":
                 str(self.home.root / "execution" / "runtime" / "runs" / "run_1" / "BRIEF.md"),
                 "workdir": "/tmp", "holon": "holon"}
        env = driver_env(d, brief)
        self.assertEqual(env["OPERANT_MEMORY_DIR"],
                         str(self.home.root / "execution" / "runtime" / "runs" / "run_1" / "memory"),
                         "the bank is the run's own memory/ dir")
        with self.assertRaises(Exception):
            bad = Driver(name="x", command="x", env={"NOPE": "{unknown_var}"})
            driver_env(bad, brief)

    def test_operant_dry_run_never_prints_a_key(self):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".key", delete=False) as fh:
            fh.write("sk-secret-value\n")
            key = fh.name
        try:
            p = subprocess.run([sys.executable, str(ADAPTERS / "operant" / "install.py"), "--holon-home",
                                str(self.home.root), "--base-url", "http://127.0.0.1:9/v1", "--model", "test-model",
                                "--api-key-file", key, "--operant", "/bin/true", "--dry-run"],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 0, p.stderr)
            self.assertNotIn("sk-secret-value", p.stdout, "a dry run must not print keys")
            cfg = tomllib.loads(p.stdout[p.stdout.index("--- observe.toml ---") + len("--- observe.toml ---"):p.stdout.index("--- act.toml ---")])
            self.assertEqual(cfg["client"]["api_key"], "<redacted>", "a dry run shows the key is wired without printing it")
        finally:
            Path(key).unlink(missing_ok=True)


class ImportCast(KernelCase):
    """H9 (phase G): operant's nine seeded seats map onto departments, roles and kernel
    mechanisms (ORG-LAYER-DISPOSITION §5), and the cast crons re-propose as routines through
    the classifier. Dry run by default; the importer lives in the adapter because the kernel
    names no harness (law 2)."""

    SEATS = [("premiere", "Premiere", "org-lead", "executive"),
             ("chief-of-staff", "Chief of Staff", "chief-of-staff", "executive"),
             ("crew-chief", "Crew Chief", "crew-chief", "crew")]

    def setUp(self):
        super().setUp()
        org = sqlite3.connect(self.tmp / "org.db")
        org.execute("CREATE TABLE employees (employee_id TEXT, name TEXT, role TEXT, department TEXT,"
                    " skills TEXT, system_prompt TEXT)")
        for sid, name, role, dept in self.SEATS:
            org.execute("INSERT INTO employees VALUES (?,?,?,?,?,?)", (sid, name, role, dept, "[]", f"charter of {sid}"))
        cron = sqlite3.connect(self.tmp / "cron.db")
        cron.execute("CREATE TABLE cron_jobs (id TEXT, name TEXT, prompt TEXT, schedule TEXT, deliver TEXT)")
        for jid, sid, what in [("cron_cast_premiere", "premiere", "daily directive + aspirations review"),
                               ("cron_cast_chief-of-staff", "chief-of-staff", "directive follow-through review"),
                               ("cron_cast_crew-chief", "crew-chief", "crew telemetry")]:
            cron.execute("INSERT INTO cron_jobs VALUES (?,?,?,?,?)", (jid, what, f"do {sid}", "0 0 9 * * *", "local"))
        cron.execute("INSERT INTO cron_jobs VALUES ('cron_other', 'capacity-retry-probe', 'retry', '0 0 * * * *', 'local')")
        org.commit(), cron.commit(), org.close(), cron.close()
        self.cmd = lambda apply: [sys.executable, str(ADAPTERS / "operant" / "import-cast.py"),
                                  "--holon-home", str(self.home.root), "--org-db", str(self.tmp / "org.db"),
                                  "--cron-db", str(self.tmp / "cron.db"), "--driver", "fake"] \
                                 + (["--apply"] if apply else [])

    def _count(self, sql):
        return self.k.wconn.execute(sql).fetchone()[0]

    def test_dry_run_maps_the_cast_and_applies_nothing(self):
        p = subprocess.run(self.cmd(False), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("routine   premiere", p.stdout)
        self.assertIn("hire      chief-of-staff", p.stdout)
        self.assertIn("mechanism crew-chief", p.stdout)
        self.assertIn("not a cast duty", p.stdout)
        self.assertIn("dry run", p.stdout)
        self.assertEqual(self._count("SELECT count(*) FROM employees WHERE name='Chief of Staff'"), 0)
        self.assertEqual(self._count("SELECT count(*) FROM tasks WHERE title LIKE 'Routine: %'"), 0)

    def test_apply_hires_the_seat_employees_and_proposes_the_cast_crons(self):
        p = subprocess.run(self.cmd(True), capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(self._count("SELECT count(*) FROM employees e JOIN departments d"
                                     " ON d.id=e.dept_id WHERE e.name='Chief of Staff' AND d.slug='executive'"), 1,
                         "the seat is an executive employee")
        for title in ("Routine: daily directive + aspirations review", "Routine: directive follow-through review"):
            self.assertEqual(self._count(f"SELECT count(*) FROM tasks WHERE title='{title}'"), 1)
        self.assertEqual(self._count("SELECT count(*) FROM tasks WHERE title='Routine: crew telemetry'"), 0,
                         "a kernel mechanism covers crew telemetry: no proposal")
        emp = self.k.wconn.execute("SELECT id FROM employees WHERE name='Chief of Staff'").fetchone()[0]
        self.assertIn("charter of chief-of-staff",
                      self.k.org.employee(emp)["job_description"], "the seat's charter is the job description")

    def test_a_seat_whose_department_is_missing_stops_the_apply_and_a_dept_map_fixes_it(self):
        org = sqlite3.connect(self.tmp / "org.db")
        org.execute("INSERT INTO employees VALUES ('identity-warden','Identity Warden','warden','it','[]','c')")
        cron = sqlite3.connect(self.tmp / "cron.db")
        cron.execute("INSERT INTO cron_jobs VALUES ('cron_cast_identity-warden','identity audit','audit','0 0 * * * *','local')")
        org.commit(), cron.commit(), org.close(), cron.close()
        p = subprocess.run(self.cmd(True), capture_output=True, text=True)
        self.assertEqual(p.returncode, 1, "a seat mapped to a department the instance lacks refuses to apply")
        self.assertIn("missing departments: it", p.stdout)
        self.assertIn("--dept-map", p.stdout)
        p = subprocess.run(self.cmd(True) + ["--dept-map", "identity-warden=executive"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("⚠ agent cadence", p.stdout, "the hourly audit is flagged for review before approving")

    def test_daily_routines_are_staggered_not_one_burst(self):
        p = subprocess.run(self.cmd(False) + ["--stagger", "7"], capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        minutes = sorted(int(m) for m in re.findall(r"on (\d+) 9 \* \* \*", p.stdout))
        self.assertEqual(minutes, [0, 7], "two daily proposals, staggered by --stagger")
        p = subprocess.run(self.cmd(False) + ["--stagger", "0"], capture_output=True, text=True)
        self.assertEqual([int(m) for m in re.findall(r"on (\d+) 9 \* \* \*", p.stdout)], [0, 0],
                         "--stagger 0 keeps the operator's cadence")


class SupervisorConformance(KernelCase):
    """H-10 (integration audit): the supervisor conformance suite. The known-good reference
    supervisor passes every clause; a supervisor that leaks its environment into the body,
    never hands in, never renews the lease, or never enforces the timeout fails."""

    def _run(self, script: str, clauses: list[str] | None = None):
        from holon.integrity import supervisor_conformance as sc
        return sc.run_supervisor(self.k, script, clauses)

    def test_the_reference_supervisor_passes_every_clause(self):
        report = self._run(f"{sys.executable} {ADAPTERS.parent / 'tests' / 'reference_supervisor.py'}")
        self.assertEqual(report["passed"], len(report["clauses"]),
                         [c for c in report["clauses"] if not c["ok"]])

    def test_a_supervisor_that_leaks_and_never_finishes_fails(self):
        leak = ("import json, os, subprocess, sys; p=json.load(sys.stdin); "
                "subprocess.run(p['command'], shell=True, cwd=p['cwd'], env=dict(os.environ))")
        report = self._run(leak, clauses=["env", "finish"])
        by = {c["clause"]: c for c in report["clauses"]}
        self.assertFalse(by["env"]["ok"], "the body saw the supervisor's whole environment")

    def test_a_supervisor_that_hangs_the_body_fails_the_timeout_clause(self):
        neglect = "import json, sys; json.load(sys.stdin); import time; time.sleep(120)"
        report = self._run(neglect, clauses=["timeout"])
        self.assertFalse(report["clauses"][0]["ok"], "nobody enforced the timeout")
