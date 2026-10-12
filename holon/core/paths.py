"""Instance layout. Every path derives from HOLON_HOME; nothing names a harness.

HOLON_HOME/
  holon.toml                     instance config
  config/drivers/*.toml             harness drivers (adapters contribute these)
  execution/runtime/work.db         delegated work (single writer: holon.execution)
  execution/runtime/runs/<run_id>/  briefs + results
  capabilities/memory/memory.db     project memory (single writer: holon.capabilities.memory)
  capabilities/strategy/trajectory.db   intent hierarchy (read-only to the kernel)
  capabilities/logbook/logbook.db       human evidence (read-only to the kernel)
  governance/law/genome.db          architecture records (P5)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


class HomeNotSet(RuntimeError):
    pass


@dataclass(frozen=True)
class Home:
    root: Path

    @classmethod
    def resolve(cls, explicit: str | os.PathLike | None = None) -> "Home":
        raw = explicit or os.environ.get("HOLON_HOME")
        if not raw:
            raise HomeNotSet("HOLON_HOME is not set (export it or pass --home)")
        return cls(Path(raw).expanduser().resolve())

    @property
    def config_file(self) -> Path:
        return self.root / "holon.toml"

    @property
    def drivers_dir(self) -> Path:
        return self.root / "config" / "drivers"

    @property
    def runtime_dir(self) -> Path:
        return self.root / "execution" / "runtime"

    @property
    def work_db(self) -> Path:
        return self.runtime_dir / "work.db"

    @property
    def runs_dir(self) -> Path:
        return self.runtime_dir / "runs"

    @property
    def memory_db(self) -> Path:
        return self.root / "capabilities" / "memory" / "memory.db"

    @property
    def trajectory_db(self) -> Path:
        return self.root / "capabilities" / "strategy" / "trajectory.db"

    @property
    def logbook_db(self) -> Path:
        return self.root / "capabilities" / "logbook" / "logbook.db"

    @property
    def genome_db(self) -> Path:
        return self.root / "governance" / "law" / "genome.db"

    def run_dir(self, run_id: str) -> Path:
        return self.runs_dir / run_id

    def ensure(self) -> None:
        for d in (
            self.drivers_dir,
            self.runs_dir,
            self.memory_db.parent,
            self.trajectory_db.parent,
            self.logbook_db.parent,
            self.genome_db.parent,
        ):
            d.mkdir(parents=True, exist_ok=True)
