---
name: gws-gmail
description: "Gmail: Send, read, and manage email."
metadata:
  version: 0.22.5
  openclaw:
    category: "productivity"
    requires:
      bins:
        - gws
    cliHelp: "gws gmail --help"
---

# Gmail

Read `../gws-shared/SKILL.md` first for authentication, command formatting, and
security guidance.

```bash
gws gmail <resource> <method> [flags]
```

## Resources

- `users.drafts` — create, list, update, send, or delete drafts.
- `users.messages` — send, retrieve, modify, or delete messages.
- `users.threads` — retrieve, modify, or delete threads.
- `users.labels` — list or manage labels.
- `users.watch` / `users.stop` — manage mailbox watches.

Before calling an API method, inspect its required parameters and request body:

```bash
gws gmail --help
gws schema gmail.<resource>.<method>
```
