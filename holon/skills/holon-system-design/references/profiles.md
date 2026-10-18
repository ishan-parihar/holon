# Bylaws profiles

```toml
name = "my-profile"
version = 1                    # bump when you change it; departments on an older version are reported
description = "when to use it"

[pipeline]                     # optional: auto (the bylaws rules decide) | always | never
research = "auto"
shape = "auto"
retro = "auto"

[[slots]]
name = "lead"                  # unique within the profile
roles = ["head", "steward"]    # any of: head researcher planner executor verifier steward
min = 1                        # employees this slot needs (default 1)
```

Holon checks that all six roles are covered (a profile with `delegates_execution = true`, like
`executive`, may omit the executor) and that no slot both executes and verifies. The standard
profiles are `light`, `standard` and `executive`; `$HOLON_HOME/bylaws/<name>.toml` adds or overrides.
