# tag

Bring Claude Code or Codex into Slack as a shared, self-hosted teammate.

Mention the bot in a channel, let it read the conversation and your approved
sources, and delegate real work without moving the discussion into one person's
private AI chat.

Tag is an open-source reference implementation inspired by
[Claude Tag](https://www.anthropic.com/news/introducing-claude-tag). It connects
Slack or Zulip to a local CLI agent and uses
[MFS](https://github.com/zilliztech/mfs) as searchable memory.

> [!WARNING]
> Tag is an alpha and is not a production security boundary. Start in an
> isolated channel, point it at a sandbox workspace, and invite only people you
> trust. The agent can read and change files using the permissions of the local
> account that runs it.

## Why I built this

I first saw Claude Tag being shared on X and wanted the same experience: mention
Claude in Slack, give it the context of the team's conversation, and let everyone
see the work happen.

The hosted launch was aimed at Claude Team and Enterprise workspaces. I was not
subscribed to one of those plans. I already had my own Claude access and wanted
to use it with my own Slack workspace.

I found the original Open Tag example in MFS, forked it, and spent about a month
adapting it to the way I work. It became useful for more than answering a single
question. Tag can read approved Slack history, follow a discussion across a
thread, retrieve related context, and share the result back where the team is
already working.

That is the part I care about: the discussion and the result stay visible in
Slack. They do not disappear into my private Claude or ChatGPT history.

## What Tag can do

- Respond when someone mentions `@OpenClaude` or `@OpenCodex` in Slack.
- Read the current thread, including text and image attachments.
- Summarize an indexed Slack channel instead of seeing only one thread.
- Search approved Slack history, repositories, documents, issues, databases,
  and object stores through MFS.
- Run real tasks through Claude Code or Codex in a configured workspace.
- Keep long answers readable by splitting them into threaded Slack replies.
- Post a requested summary back into the current channel.
- Run the same memory and agent backend through Zulip.

## See it in action

### Delegate work across channels

A teammate requests a PR review in one channel. From another channel, someone
mentions the bot and asks it to handle the review. Tag finds the original
request in indexed Slack history, retrieves the PR context, and reports back in
the thread.

![Tag reviewing a PR using context from another Slack channel](https://github.com/user-attachments/assets/6cb1db05-dd12-4a13-a9fa-1a1bf69bcf28)

### Continue the discussion with shared context

A follow-up asks the bot to compare two projects and write up the differences.
Tag keeps the thread context, gathers information from the approved sources, and
returns the result where the rest of the team can read and continue the work.

![Tag completing a follow-up task across multiple sources](https://github.com/user-attachments/assets/8f11e931-4248-46c5-b1fb-8128d56b8773)

## How it works

```text
       ┌──────────────┐
       │    Slack     │   @OpenClaude <task>
       │   or Zulip   │ ◄──── answer ──────┐
       └──────┬───────┘                    │
              │ mention                    │
              ▼                            │
   ┌────────────────────────────────────┐  │
   │                Tag                 ├──┘
   │   Brain: Claude Code or Codex CLI  │
   └────────────────┬───────────────────┘
                    │ scoped retrieval
                    ▼
   ┌────────────────────────────────────┐
   │                MFS                 │
   │ Slack · repos · docs · issues · DB │
   └────────────────────────────────────┘
```

Tag has three parts:

- **Brain:** Claude Code or Codex runs the task locally.
- **Memory:** MFS indexes the sources you approve and makes them searchable.
- **Chat:** Slack or Zulip supplies the conversation and receives the answer.

Tag does not call a model API directly. Authentication, model access, and usage
come from the CLI backend installed on your machine.

## Quick start

The guided setup currently targets macOS. The underlying Python scripts can also
be run manually on other platforms.

### 1. Clone Tag

```bash
git clone https://github.com/klovr-co/tag.git
cd tag
```

### 2. Install the admin skill for Codex

```bash
npx skills add klovr-co/tag --skill open-tag-admin -a codex -g
```

Open a new Codex task and ask:

> Set up Tag for Slack using Codex. I do not have the Slack credentials yet, so
> walk me through creating the app and run every preflight check before starting
> the bot.

The `open-tag-admin` skill guides the setup. It cannot create or approve a Slack
app on behalf of your workspace administrator.

### 3. Run guided setup

```bash
open "OpenTag Setup.command"
```

The setup checks local prerequisites and creates a private `.env` file with
owner-only permissions. It will pause when you need to create the Slack app,
install it to your workspace, or supply credentials.

You will need:

- [`uv`](https://docs.astral.sh/uv/);
- a working `claude` or `codex` CLI login;
- an MFS server with at least one indexed source;
- a Slack app with Socket Mode enabled;
- a private or otherwise isolated Slack channel for the first run.

### 4. Start Tag

```bash
open "OpenTag Control.command"
```

Choose **Start Open Tag**, then mention `@OpenClaude` or `@OpenCodex` in the
configured Slack channel.

Try:

> @OpenCodex summarize this channel and list the decisions and open questions.

Or:

> @OpenClaude read this thread, inspect the linked repository, and propose the
> smallest fix.

## Slack credentials

Tag uses Slack credentials in two separate places:

| Credential | Purpose |
|---|---|
| `SLACK_APP_TOKEN` (`xapp-…`) | Opens the Socket Mode connection that receives mentions. |
| `SLACK_BOT_TOKEN` (`xoxb-…`) | Reads permitted conversations and posts replies. |
| MFS Slack connector token | Optionally indexes approved Slack channels as durable memory. |

The bridge app normally needs these bot scopes:

- `app_mentions:read`
- `chat:write`
- `channels:read` and `channels:history`
- `groups:read` and `groups:history` if you intentionally use private channels

It also needs the `app_mention` bot event and an app-level token with
`connections:write`. Invite the bot only to channels where it should respond.

For the complete setup, token model, and troubleshooting checklist, read
[the Slack adapter guide](references/slack-adapter.md).

## Give Tag memory

Tag can only retrieve sources that meet both conditions:

1. the source has already been indexed by MFS; and
2. its root is listed in `MFS_ALLOWED_SCOPES`.

For example:

```bash
export MFS_ALLOWED_SCOPES="slack://team-memory,file://local/path/to/repo"
```

MFS supports Slack, local files, GitHub, Jira, Linear, Postgres, MongoDB,
BigQuery, S3, and other connectors. Connector credentials remain under your
control. Tag consumes indexed sources; it does not silently add new ones.

The scope helper rejects reads and directory listings outside the configured
roots. The underlying connector credentials and source allowlists remain an
additional boundary.

See [Memory](references/memory.md) for the retrieval model and the
[MFS connector documentation](https://github.com/zilliztech/mfs/tree/main/docs/connectors/)
for available sources.

## Optional: use Zulip

Tag can use the same backend and MFS memory from Zulip. The native adapter starts
a fresh bounded agent for every direct message or stream mention. The optional
ZulipMCP adapter keeps a persistent mention-activated session per stream topic.

Set `OPENTAG_TRANSPORT` to `zulip` or `both`, provide a Generic bot `zuliprc`,
and choose `OPENTAG_ZULIP_ENGINE=native` or `zulipmcp`. Start with a private
sandbox stream and explicit read/write allowlists.

See [the ZulipMCP runtime guide](references/zulipmcp-runtime.md) and the
`open-tag-admin` skill for the full setup.

## Security model

Tag turns chat messages into instructions for a local coding agent. Treat every
message and attachment as untrusted input.

Current safeguards include:

- MFS scope checks for search, read, and directory listing;
- an optional `SLACK_CHANNEL_ID` gate;
- transport-specific credential isolation;
- bounded attachment size and thread context;
- task timeouts and limited retries;
- automatic Codex workspace safety review.

Tag does **not** provide a hardened sandbox, organization-wide identity policy,
auditable approvals, spend controls, or enterprise administration. Claude Code
currently runs with permission checks skipped. Locally installed tools use their
own credentials and permissions.

Use a non-production host or a real external sandbox for stronger isolation.

## Documentation

- [Slack setup and troubleshooting](references/slack-adapter.md)
- [Backend behavior](references/backends.md)
- [Runtime agent contract](references/runtime-agent.md)
- [Memory model](references/memory.md)
- [ZulipMCP runtime](references/zulipmcp-runtime.md)
- [Included skills](docs/skills.md)

## Origins and attribution

Tag began as a modified derivative of the
[Open Tag Example](https://github.com/zilliztech/mfs/tree/main/examples/open-tag-skill)
from [Zilliz MFS](https://github.com/zilliztech/mfs). The upstream material is
licensed under the Apache License 2.0. This repository contains subsequent
modifications and extensions.

## Status

Tag is an early open-source project built from a workflow that has already been
useful in day-to-day Slack discussions. It is ready for experimentation in a
trusted sandbox—not as a production security boundary.

Issues and contributions are welcome.
