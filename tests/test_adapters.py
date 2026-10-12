"""R6: the adapter shape. Shipped drivers load, installers produce valid configuration, and the
conformance suite tells a compliant harness from a broken one."""

import io
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
        self.assertEqual({p.parent.name for p in found}, {"zeroclaw", "hermes", "claude-code"})
        for p in found:
            d = Driver.from_toml(p.read_text(), str(p))
            self.assertEqual(d.stages, list(vocab.STAGES), p)
            self.assertTrue(any(x in d.command for x in ("{briefing}", "{identity}", "{identity_md}")),
                            f"{p}: the identity card must reach the agent")

    def test_zeroclaw_installer_produces_valid_config_with_safe_roots(self):
        zc = self.tmp / "zeroclaw"
        zc.mkdir()
        with open(self.home.config_file, "a") as fh:
            fh.write('\n[scope.dept.eng]\nroots = ["/srv/shared"]\ncommands = ["arch"]\nact_commands = ["systemctl"]\n')
        (zc / "config.toml").write_text('[providers.models.openai.main]\nuri = "http://x"\nmodel = "m"\napi_key = "sk-secret"\n\n'
                                        '[agents.assistant]\nmodel_provider = "openai.main"\n')
        p = subprocess.run([sys.executable, str(ADAPTERS / "zeroclaw" / "install.py"), "--holon-home",
                            str(self.home.root), "--config-dir", str(zc), "--allow", str(self.workdir),
                            "--model", "free-stack", "--dry-run"], capture_output=True, text=True)
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
        self.assertEqual(cfg["agents"]["holon-eng-act"]["risk_profile"], "holon-eng-worker")
        self.assertIn('scope_fingerprint = ""', (ADAPTERS / "zeroclaw" / "driver.toml").read_text(),
                      "install.py stamps the fingerprint by replacing this exact line")
        self.assertEqual(cfg["providers"]["models"]["openai"]["holon"]["model"], "free-stack")
        server = next(m for m in cfg["mcp"]["servers"] if m["name"] == "holon")
        self.assertEqual((server["transport"], server["args"]), ("stdio", ["mcp"]))
        self.assertTrue(server["command"].endswith("bin/holon"))
        self.assertEqual(cfg["mcp_bundles"]["holon"]["servers"], ["holon"])
        for alias in ("holon-org-observe", "holon-org-act", "holon-eng-observe", "holon-eng-act"):
            self.assertEqual(cfg["agents"][alias]["mcp_bundles"], ["holon"])

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
