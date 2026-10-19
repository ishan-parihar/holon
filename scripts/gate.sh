#!/bin/sh
# The gate this repo's own AGENTS.md names, as one command: the unit suite, then
# the strict lint against a throwaway home. `genome close` runs this.
#
# The two commands are kept together here so the gate is one thing to run and
# one thing to read. The home is a fresh temporary directory every run, so the
# lint never sees the developer's own state.
set -e
cd "$(dirname "$0")/.."

python3 -m unittest discover -s tests -t .
python3 -m holon --home "$(mktemp -d)" lint --strict
