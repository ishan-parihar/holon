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
        self.assertIn(f"{ROOT}/bin/holon tick", units["holon-tick.service"])
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
