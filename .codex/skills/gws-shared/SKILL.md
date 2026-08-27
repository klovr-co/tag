---
name: gws-shared
description: "gws CLI: Shared patterns for authentication, global flags, and output formatting."
metadata:
  version: 0.22.5
  openclaw:
    category: "productivity"
    requires:
      bins:
        - gws
    cliHelp: "gws --help"
---

# gws — Shared Reference

The `gws` binary must be on `$PATH`.

## Authentication

```bash
gws auth login
```

## Command format

```bash
gws <service> <resource> [sub-resource] <method> [flags]
```

Use `--params '{"key": "value"}'` for query parameters and `--json '{"key":
"value"}'` for JSON request bodies. Use `--dry-run` when supported to validate
a request without calling the API.

Never output secrets, API keys, or tokens.
