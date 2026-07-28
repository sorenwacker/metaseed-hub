# Connecting an agent (MCP)

The hub exposes a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/hub/mcp`, so an agent such as Claude can read and write your datasets without a browser.

## Getting a token

The hub signs you in through your institution, which needs a browser; an agent is not a browser, so it presents a **personal access token** instead. Create one under **Access tokens** on your [profile](getting-started.md#your-profile).

The token is shown once and cannot be recovered — only its hash is stored, so a copy of the database is not a set of working credentials. Create a new one if you lose it, and revoke any you no longer use.

A token acts as **you**. Every tool call is scoped to your own workspace: it can see and change your datasets and nothing else.

## Configuring Claude Code

```bash
claude mcp add --transport http metaseed-hub https://metaseed.ewi.tudelft.nl/hub/mcp \
  --header "Authorization: Bearer msh_your_token_here"
```

## What an agent can do

| Tool | |
| --- | --- |
| `whoami` | Which account the token acts as |
| `list_datasets` | Your datasets |
| `get_dataset` | A dataset's stored contents |
| `create_dataset` | A new, empty dataset |
| `save_dataset` | Replace a dataset's contents |
| `list_profiles` | Built-in standards, plus every published specification |
| `get_profile_schema` | A profile's entity types and their fields |

Published specifications are included in `list_profiles`, because publishing shares a specification with every user of the hub — an agent can build a dataset against one it did not write.

## Revoking access

Revoke a token from your profile. It stops working immediately; the record is kept so an administrator can see that it existed and when it was withdrawn.
