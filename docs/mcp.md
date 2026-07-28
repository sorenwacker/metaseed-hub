# Connecting an agent (MCP)

The hub exposes a [Model Context Protocol](https://modelcontextprotocol.io) endpoint at `/hub/mcp`, so an agent such as Claude can read and write your datasets without a browser.

## Getting a token

The hub signs you in through your institution, which needs a browser; an agent is not a browser, so it presents a **personal access token** instead. Create one under **Access tokens** on your [profile](getting-started.md#your-profile).

The token is shown once and cannot be recovered — only its hash is stored, so a copy of the database is not a set of working credentials. Create a new one if you lose it, and revoke any you no longer use.

A token can be given an expiry, after which it stops working on its own. A token without one lasts until revoked, which is what a token pasted into a config file and forgotten then does indefinitely.

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
| `validate_dataset` | Check a dataset against its profile and list what is missing |
| `delete_dataset` | Remove a dataset (soft — it is not erased) |
| `list_profiles` | Built-in standards, plus every published specification |
| `get_profile_schema` | A profile's entity types and their fields |

Published specifications are included in `list_profiles`, because publishing shares a specification with every user of the hub — an agent can build a dataset against one it did not write.

## What an agent cannot do to your work

An agent replaces a whole dataset in one call, and can do it in a loop, so the write path is built to leave a way back:

- **Every overwrite keeps the previous contents** as a version, restorable from the dataset's history in the web interface. A save that changes nothing adds no version, so repeated writes cannot bury the history.
- **Deleting is soft.** The dataset stops being listed but is not erased.
- **A dataset larger than 5 MB is refused** rather than stored, so a runaway loop is stopped.
- **Every write is logged** with the account it acted as.

A token reaches only its own user's datasets. Another person's dataset is not readable, writable, or deletable, and a name that exists in someone else's workspace reads as absent.

## Using a token with the REST API

The same token authenticates the REST API at `/api`, so a script can do anything the web interface can:

```bash
curl -H "Authorization: Bearer msh_your_token" \
  https://metaseed.ewi.tudelft.nl/api/datasets
```

The hub accepts exactly two credentials: a SRAM access token, which a browser obtains through the sign-in flow, and a token from this page. A token acts for your own data only and never carries administrator rights, whatever your account holds.

## Revoking access

Revoke a token from your profile. It stops working immediately; the record is kept so an administrator can see that it existed and when it was withdrawn.
