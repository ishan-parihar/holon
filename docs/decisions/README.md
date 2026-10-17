# Decisions — AD-NNN records

Immutable once numbered. A decision record holds a law an agent can apply
without asking: context, decision, consequences. Correcting a decision means
minting a new AD that supersedes the old one by name — never an edit.

`scripts/kb ad "<title>"` mints the next one from the template.

Nothing is migrated retroactively: decisions already embedded in
`docs/INTENT.md`, `docs/GENOME.md`, `docs/CONTRACT.md` and the dated design
records stay exactly where they are enforced. ADs start from the first Chair
ratification after 2026-10-17 — the audit §2 items (`TODO.md` H1–H6) are the
first candidates.
