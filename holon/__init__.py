"""holon kernel: a harness-agnostic agentic operating system.

The kernel is Python stdlib + sqlite3 only. It never imports, spawns by name,
or reads the state of any agent harness; harnesses plug in as driver files.
"""

__version__ = "0.1.0"
