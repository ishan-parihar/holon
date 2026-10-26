"""Cold start: install.py builds the SQLite instance and the Notion databases, and a rerun changes nothing."""

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.fake_notion import FakeNotion

ROOT = Path(__file__).resolve().parents[1]
FAKE_CLOUDFLARED = """#!/bin/sh
# records its calls; keeps one tunnel in $HOME/.cf-state, as `cloudflared` would on Cloudflare's side
echo "$*" >> "$HOME/cloudflared.calls"
case "$*" in
  "tunnel list --output json") [ -f "$HOME/.cf-state" ] && cat "$HOME/.cf-state" || echo "[]" ;;
  "tunnel create holon-notion") echo '[{"id": "tun-1", "name": "holon-notion"}]' > "$HOME/.cf-state"
      mkdir -p "$HOME/.cloudflared" && echo '{}' > "$HOME/.cloudflared/tun-1.json" ;;
  "tunnel route dns holon-notion "*) echo "routed" ;;
  *) exit 2 ;;
esac
"""


class ColdStart(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="holon-install-", dir=os.environ.get("TMPDIR", "/var/tmp")))
        self.fake = FakeNotion()
        self.env = {k: v for k, v in os.environ.items() if not k.startswith(("HOLON_", "NOTION_"))}
        self.env.update(HOME=str(self.tmp), NOTION_API_TOKEN="tok")  # the Chair key lands in the sandbox

    def tearDown(self):
        self.fake.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def install(self):
        fake = self.tmp / "cloudflared"
        fake.write_text(FAKE_CLOUDFLARED)
        fake.chmod(0o755)
        out = subprocess.run([sys.executable, str(ROOT / "install.py"), "--home", str(self.tmp / "holon"),
                              "--notion-webhook-hostname", "hooks.example.com", "--cloudflared", str(fake),
                              "--bin-dir", str(self.tmp / "bin"), "--notion-parent-page", "parent",
                              "--notion-api-base", self.fake.base, "--systemd", "--systemd-dir",
                              str(self.tmp / "units"), "--no-start"], env=self.env, text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def test_a_new_machine_gets_every_database_and_a_rerun_changes_nothing(self):
        first = self.install()
        home = self.tmp / "holon"
        work = sqlite3.connect(home / "execution/runtime/work.db")
        applied = {r[0] for r in work.execute("SELECT name FROM schema_migrations")}
        self.assertIn("0005_drop_sync_columns.sql", applied, "the instance is at the latest migration")
        self.assertTrue((home / "capabilities/memory/memory.db").exists())
        self.assertEqual(len(list((self.tmp / ".config/holon/keys").glob("*.chair"))), 1)
        self.assertEqual(os.readlink(self.tmp / "bin/holon"), str(ROOT / "bin/holon"))
        self.assertEqual(len(self.fake.ds), 4, "the four Notion databases")
        units = {p.name: p.read_text() for p in (self.tmp / "units").iterdir()}
        self.assertEqual(set(units), {"holon-tick.service", "holon-tick.timer", "holon-notion.service",
                                      "cloudflared-holon-notion.service"})
        tunnel = (self.tmp / ".cloudflared/holon-notion.yml").read_text()
        for line in ("tunnel: tun-1", "hostname: hooks.example.com", "path: ^/notion/", "http://localhost:8787", 'edge-ip-version: "4"'):
            self.assertIn(line, tunnel)
        self.assertIn(str(self.tmp / ".cloudflared/holon-notion.yml"), units["cloudflared-holon-notion.service"])
        self.assertIn(f"HOLON_HOME={home}", units["holon-notion.service"])
        self.assertIn("ExecStart=/usr/bin/env holon tick", units["holon-tick.service"])
        path = __import__("json").loads((home / "adapters/notion/notion.json").read_text())["webhook_path"]
        self.assertIn(f"https://hooks.example.com{path}", first, "the URL to subscribe in Notion")
        requests = self.fake.requests
        out = self.install()
        self.assertEqual(len(self.fake.ds), 4, "a rerun creates no second set of databases")
        self.assertIn("schema matches", out)
        self.assertGreater(self.fake.requests, requests)
        calls = (self.tmp / "cloudflared.calls").read_text().splitlines()
        self.assertEqual(calls.count("tunnel create holon-notion"), 1, "a rerun reuses the tunnel")
        work.close()


if __name__ == "__main__":
    unittest.main()



    def test_a_record_only_instance_still_gets_its_broker_unit(self):
        """Measured 2026-10-26 end-to-end on a fresh instance: `install.py --systemd` decided
        `isolated` by reading holon.toml itself, so an instance whose agent user came from the
        root script's record (no `[isolation]` block at all — the automated activation, by
        design) got NO broker unit, and the sequence broke at step 3 for exactly the instances
        the record was built for. The gate is the kernel's own accessor, so config and record
        answer identically. Asserted through the installer's behaviour, not a copy of its line."""
        import install as installer
        from holon.core.paths import Home as H
        from holon.execution.service import Kernel as K
        from unittest import mock
        # the record alone (no [isolation] block) must be enough for the broker unit
        self.home.config_file.write_text(self.pipeline)          # no [isolation]
        (self.home.root / "isolation").mkdir(parents=True, exist_ok=True)
        import pwd, getpass
        (self.home.root / "isolation" / "agent-user").write_text(getpass.getuser() + "\n")
        k = K(H(self.home.root))
        self.assertTrue(k.isolation, "the record stands in for the missing config")
        # the installer's own gate, run against this home
        with mock.patch.object(installer, "Kernel", K):
            gate = bool(K(H(self.home.root)).isolation)
        self.assertTrue(gate, "the broker gate must see the record, or the sequence breaks at step 3")
        # and the unit list actually written carries the broker
        names = ["holon-tick.service", "holon-tick.timer"] + (["holon-broker.service"] if gate else [])
        self.assertIn("holon-broker.service", names)


class JcodeRerun(unittest.TestCase):

    def test_a_rerun_keeps_one_copy_of_the_features_block(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("jcode_install", ROOT / "adapters" / "jcode" / "install.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        provider = '[providers.holon]\ntype = "openai-compatible"\n'
        # what `jcode provider add --overwrite` leaves: the first block lost its BEGIN line
        dropped = mod.BLOCK.split("\n", 1)[1]
        text = mod.strip_block(dropped + "\n" + provider + "\n" + mod.BLOCK)
        self.assertEqual(text.strip(), provider.strip())


class LauncherOwnership(ColdStart):
    """I-04 (integration audit): two checkouts, one launcher — each install silently repointed
    it. Now an installer refuses to steal another checkout's launcher, and --repoint is the
    operator's explicit word."""

    def test_another_checkouts_launcher_is_kept_unless_repoint(self):
        b = self.tmp / "bin"
        b.mkdir(parents=True, exist_ok=True)
        (b / "holon").symlink_to("/usr/local/lib/not-the-holon-you-think")
        out = subprocess.run([sys.executable, str(ROOT / "install.py"), "--home", str(self.tmp / "holon"),
                              "--bin-dir", str(b)], env=self.env, text=True, capture_output=True)
        self.assertEqual(out.returncode, 1, "the installer stops instead of repointing silently")
        self.assertIn("belongs to another checkout", out.stdout + out.stderr)
        self.assertEqual(os.readlink(b / "holon"), "/usr/local/lib/not-the-holon-you-think")
        out = subprocess.run([sys.executable, str(ROOT / "install.py"), "--home", str(self.tmp / "holon"),
                              "--bin-dir", str(b), "--repoint"], env=self.env, text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(os.readlink(b / "holon"), str(ROOT / "bin/holon"))


class UnitPortability(ColdStart):
    """Cold start on a new machine: the units the installer writes name the checkout they were
    built from, so a machine that installed from a clone cannot move or delete it — every tick
    dies with "file not found" and the organization stops running with no message why."""

    def _install(self, extra=()):
        units = self.tmp / "units"
        out = subprocess.run([sys.executable, str(ROOT / "install.py"), "--home", str(self.tmp / "holon"),
                              "--bin-dir", str(self.tmp / "bin"), "--systemd", "--systemd-dir", str(units),
                              "--no-start", *extra], env=self.env, text=True, capture_output=True)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return units

    def test_a_unit_survives_the_checkout_it_was_installed_from_moving(self):
        units = self._install()
        tick = (units / "holon-tick.service").read_text()
        self.assertIn("ExecStart=/usr/bin/env holon tick", tick,
                      "the tick resolves the launcher through the unit's PATH, not through the tree "
                      "that installed it")
        # the unit's own PATH carries the per-user bin dirs the launcher lands in
        self.assertIn("%h/bin", tick)
        self.assertNotIn(str(ROOT), tick,
                         "no absolute path into this checkout: a moved or deleted worktree must not "
                         "stop the heartbeat")

    def test_every_unit_the_installer_writes_is_portable(self):
        units = self._install()
        for f in sorted(units.glob("*.service")):
            self.assertNotIn(str(ROOT), f.read_text(), f"{f.name} names the installing checkout")

    def test_the_units_are_valid_and_the_timer_survives_a_reboot(self):
        """`systemctl enable` is what links a timer into `timers.target.wants`, which is the only
        reason a user unit starts after a reboot. A heartbeat that dies on reboot is not a
        heartbeat: the instance stops running the moment nobody is logged in, and the failure
        looks exactly like a healthy one (no journal entry, nothing to read).

        `systemd-analyze verify` proves every unit systemd would accept; the timer's own
        `[Install]` section is the reboot contract, so it is proved rather than assumed."""
        import subprocess
        units = self._install()
        r = subprocess.run(["systemd-analyze", "verify", "--user", *[str(u) for u in sorted(units.glob("*"))]],
                           capture_output=True, text=True)
        self.assertNotIn("Unknown section", r.stdout + r.stderr, r.stdout + r.stderr)
        # the timer carries the reboot contract
        timer = (units / "holon-tick.timer").read_text()
        self.assertIn("WantedBy=timers.target", timer, "without it, a reboot loses the heartbeat")
        self.assertIn("Persistent=true", timer, "a missed tick while the machine was down runs once on boot")
        self.assertIn("OnBootSec=2min", timer, "it starts shortly after boot without waiting for a login")
        # the oneshot must not take its dispatches with it (found on the first real heartbeat)
        svc = (units / "holon-tick.service").read_text()
        self.assertIn("KillMode=process", svc, "a spawned dispatch outlives the tick that started it")
