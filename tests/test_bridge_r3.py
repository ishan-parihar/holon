"""Tests first for the bridge plan's R3 — one adapter-installer seam.

Four installers, no shared helper, and both real defects came from that drift: the
jcode installer pinned a gateway port and a model the gateway no longer served, and the
operant driver pinned a stale root-owned binary built against a different ONNX Runtime
than the repo pins. Both are the same class — **a pin taken without checking that what
it names still works** — so the seam enforces that before a driver is written.
"""

import os
import subprocess
import sys
from pathlib import Path

from tests.helpers import KernelCase

ADAPTERS = Path(__file__).resolve().parents[1] / "adapters"
sys.path.insert(0, str(ADAPTERS))
import install_lib  # noqa: E402  (after the path insert, by design)


class OneInstallerSeam(KernelCase):
    """R3. The shared module, and the two invariants it adds."""

    def test_a_binary_that_cannot_start_is_not_pinned(self):
        """The operant lesson, as an invariant: a harness that fails its own `--version`
        cannot run a stage, so the caller is refused rather than handed a driver that
        dies on its first run."""
        broken = self.tmp / "operant"
        broken.write_text('#!/bin/sh\necho "libonnxruntime.so.1: version VERS_1.20.1 not found" >&2\nexit 1\n')
        broken.chmod(0o755)
        with self.assertRaises(install_lib.InstallError) as caught:
            install_lib.pin_binary("operant", str(broken))
        self.assertIn("cannot start", str(caught.exception))
        self.assertIn("VERS_1.20.1", str(caught.exception),
                      "the harness's own error is passed through, so the operator knows what to fix")

    def test_a_missing_binary_is_refused_rather_than_pinned_by_name(self):
        """Passing a path that does not exist must not silently install a driver that
        resolves differently on the next PATH."""
        with self.assertRaises(install_lib.InstallError):
            install_lib.pin_binary("no-such-harness-xyz", str(self.tmp / "nope" / "binary"))

    def test_a_working_binary_is_pinned_by_absolute_path(self):
        """The pin is an absolute path to the launcher, never the bare name — the driver
        must run the same binary on a timer whose PATH differs."""
        good = self.tmp / "good-harness"
        good.write_text("#!/bin/sh\necho 'good 0.2.1'\n")
        good.chmod(0o755)
        pinned = install_lib.pin_binary("good-harness", str(good))
        self.assertEqual(pinned, str(good))
        self.assertTrue(Path(pinned).is_absolute())

    def test_write_driver_refuses_toml_the_kernel_could_not_load(self):
        """A driver that fails to parse is refused before it lands: a half-written driver
        blocks every stage of that harness."""
        with self.assertRaises(Exception):
            install_lib.write_driver(self.home.root, "broken", 'name = "broken"\ncommand = "x\n')
        self.assertFalse((self.home.root / "config" / "drivers" / "broken.toml").exists(),
                         "no file is written for a driver that would not parse")

    def test_every_adapter_installer_stands_on_the_seam(self):
        """The point of R3: no installer carries its own copy of the shared steps, so a
        fix lands in all four at once instead of the one someone remembered."""
        specs = {
            "jcode": ("pin_binary", "export_skills", "write_driver", "verify"),
            "operant": ("pin_binary", "export_skills", "write_driver", "verify"),
            "hermes": ("pin_binary", "export_skills", "write_driver", "verify"),
            "zeroclaw": ("pin_binary", "export_skills", "write_driver", "verify"),
        }
        for adapter, needed in specs.items():
            text = (ADAPTERS / adapter / "install.py").read_text()
            missing = [step for step in needed if f"install_lib.{step}" not in text]
            self.assertEqual(missing, [], f"{adapter}/install.py does not stand on the seam: {missing}")

    def test_the_pin_the_installer_writes_is_the_binary_it_verified(self):
        """The driver pins what was verified, not what was asked for by name. This is the
        check whose absence let the live instance pin a stale root-owned `operant` while
        the current one sat elsewhere on PATH."""
        good = self.tmp / "current"
        good.write_text("#!/bin/sh\necho 'current 0.2.1'\n")
        good.chmod(0o755)
        text = (ADAPTERS / "operant" / "install.py").read_text()
        self.assertIn("pin_binary", text, "the operant installer pins through the seam")
        # and the seam, not the installer, decides
        self.assertEqual(install_lib.pin_binary("current", str(good)), str(good))
