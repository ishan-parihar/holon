# Genome profiles

```toml
name = "my-profile"
description = "when to use it"

[[slots]]
name = "lead"                 # unique within the profile
roles = ["head", "steward"]   # any of: head researcher planner executor verifier steward
min = 1                       # employees this slot needs (default 1)
```

Rules checked by Holon: all six roles are covered (a profile with `delegates_execution = true`,
like `executive`, may omit the executor); no slot both executes and verifies.
Stdlib profiles: `light`, `standard`. Instance profiles in `$HOLON_HOME/genome/*.toml` override
them by name.
