# Contributing

Holon is a small, self-checking kernel. The bar for every contribution is that it stays that way.

## The gates (CI runs them on every push and pull request)

```sh
python3 -m unittest discover -s tests -t .
python3 -m holon --home "$(mktemp -d)" lint --strict   # zero errors and zero warnings
```

Tested on Python 3.11 and 3.13. `holon lint` enforces most of the laws below mechanically.

## Laws (AGENTS.md has the full list)

- **Stdlib only** under `holon/`: no third-party imports, no new dependencies.
- **No harness named in the kernel.** Harness specifics live in `adapters/`; an adapter is
  tested by `holon adapter test <name>`.
- **Tests first** for any change to the work graph, the contract, security or drivers.
- **A defect found by a real run gets a guard test and a ledger row in the same commit.** The
  regression ledger in AGENTS.md is checked by `holon lint` (`ref.guard`): every row names a
  test, and every test it names must exist, so a guard cannot be deleted silently.
- **Schema changes are new migration files**, never edits to applied ones.
- **New config keys are documented** in `docs/GENOME.md` §6, or lint fails.

## No personal or instance data

This repository is the **universal Holon**: usable by anyone, free of every operator's context.
AGENTS.md already keeps instance specifics out of the code; contributions are held to
the same standard:

- no hosts, paths, chat ids, endpoints or keys;
- no deployment or operations notes — each operator keeps those in their private overlay
  repository (AGENTS.md § "The universal repo and the personal overlay");
- instance specifics (departments, tailored skills, configuration) live in each operator's
  `$HOLON_HOME`, never here.

A pull request that adds any of the above will be asked to move it out before merge.

## Pull requests

Small and focused, with the failing case covered by a test. If your change comes from running a
real harness, record in the adapter's README what the harness did that the contract did not
expect — that is where real-harness lessons live.
