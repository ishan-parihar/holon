# 0006: checksum repair for memory/0003 (found on the live workstation instance, 2026-10-25 —
# the successor outline's F1). The instance applied a pre-commit DRAFT of 0003 at
# 2026-10-15T14:29Z; the file was committed 52 minutes later with a different checksum, and the
# applied row's checksum (d3d6c1492905…) matched no version of the file that ever existed in
# git. `migrate()` re-stamps only `checksum IS NULL` rows, so a drifted non-NULL row made
# `holon lint --strict` fail on the live home forever, and no command could repair it.
#
# This repair follows 0004's precedent (a repair migration that is a no-op on a healthy
# instance), and its honesty rests on the same ordering argument: migrations apply in order, so
# any instance applying this file has ALREADY had 0004 unconditionally re-assert the api_memory
# view from the shipped definition — the semantics the drifted checksum claims to record are
# provably the shipped ones. The re-stamp reads the shipped file at apply time, so no hash is
# hardcoded here.
#
# Laundering surface, stated: this runs once, and only ever touches `0003_api_memory.sql`'s
# row. An edit to ANY migration file made after this one is applied remains exactly the error
# lint reports today (law 6) — a later edit to 0003 itself included, because this file never
# runs again.

def up(conn):
    import hashlib
    from importlib import resources
    text = resources.files("holon.migrations").joinpath("memory").joinpath(
        "0003_api_memory.sql").read_text()
    shipped = hashlib.sha256(text.encode()).hexdigest()
    conn.execute(
        "UPDATE schema_migrations SET checksum=? WHERE name='0003_api_memory.sql' AND checksum<>?",
        (shipped, shipped))
