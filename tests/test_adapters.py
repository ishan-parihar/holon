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
        self.home.genome_units_dir.mkdir(parents=True)
        (self.home.genome_units_dir / "vault.toml").write_text(
            f'charter = "the research substrate"\ncategory = "service"\ntier = "foundation"\n'
            f'roots = ["{self.tmp / "data" / "vault"}"]\npool_of = ["eng"]\nconsumed = []\n\n'
            f'[[services]]\nid = "kosmos"\ncommand = "kosmos --query"\naccepts = "q"\nreturns = "r"\n')
        from holon.genome import units
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

    def test_cli(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--home", str(self.home.root), "adapter", "test", "fake", "--clauses", "driver,plan"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("2/2 passed", buf.getvalue())


class Operant(KernelCase):
    def test_operant_installer_isolates_and_writes_both_postures(self):
        p = subprocess.run([sys.executable, str(ADAPTERS / "operant" / "install.py"), "--holon-home",
                            str(self.home.root), "--base-url", "http://127.0.0.1:9/v1", "--model", "test-model",
                            "--api-key-file", "/dev/null", "--operant", "/bin/true", "--dry-run"],
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stderr)
        obs = tomllib.loads(p.stdout[p.stdout.index("--- observe.toml ---") + len("--- observe.toml ---"):p.stdout.index("--- act.toml ---")])
        act = tomllib.loads(p.stdout[p.stdout.index("--- act.toml ---") + len("--- act.toml ---"):])
        # observe: every mutating tool is gone; act: only the AFT writers are
        self.assertIn("file_write", obs["tools"]["disabled_tools"])
        self.assertIn("terminal", obs["tools"]["disabled_tools"])
        self.assertIn("send_message", obs["tools"]["disabled_tools"])
        self.assertNotIn("file_write", act["tools"]["disabled_tools"])
        # an approved plan never stalls on a prompt an unattended harness cannot answer.
        # command_allowlist is a top-level key (it seeds the agent's approval allowlist)
        self.assertEqual(act.get("command_allowlist"), ["*"])
        self.assertNotIn("command_allowlist", obs)
        # the organization's records are Holon's, and the body's own lanes stay pinned off: the
        # continual-harness lane (KernelSettings lives at [tools.kernel] — config.rs's
        # ToolSettings.kernel; a top-level [kernel] is an unknown field and operant refuses the
        # whole config, the live day's sixth body died at startup on it) is off, and
        # [memory] carries no `provider` key — current operant ignores it ("memory-wire is the only
        # memory engine now"), so the pin promised isolation that did not exist (R-08/R-10)
        for cfg in (obs, act):
            self.assertEqual(cfg["genome"]["unrestricted_default"], "lockdown")
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
            self.assertNotIn("{", v, f"{k} still carries a template")
            self.assertNotIn("}", v, f"{k} still carries a template")
        self.assertEqual(d.env["HERMES_HOME"], str(self.home.root / "adapters" / "operant" / "home"),
                         "an isolated operant home per instance")

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
