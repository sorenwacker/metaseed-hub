# MCP FAQ

Answers to the questions that come up when connecting an agent to the hub. For setup and the tool reference, see [Connecting an agent (MCP)](mcp.md).

## Connection and authentication

**Where is the endpoint?** `https://metaseed.ewi.tudelft.nl/hub/mcp`, over streamable HTTP. There is no SSE or stdio transport; the endpoint is part of the hub server.

**Why do I get "No bearer token" or a refused call?** Every call needs an `Authorization: Bearer msh_...` header carrying a personal access token. There is no anonymous access and no default account: a request without a valid token fails rather than acting as anyone.

**My token stopped working.** Tokens stop working when revoked or expired. The plain token cannot be recovered from the hub (only its hash is stored) — create a new one under Access tokens on your profile and replace it in your client config.

**Can I use my institutional login instead of a token?** No. Institutional sign-in needs a browser; agents are not browsers. The token stands in for that sign-in and acts as exactly your account.

## Scope and isolation

**What can an agent see?** Exactly what you can: datasets in your own tenant, your own spec drafts, and every published specification. Other tenants' datasets are invisible, and other users' drafts are not editable even inside your tenant.

**Do agent edits overwrite my colleagues' work?** Edits go through the same storage as the web UI, entity by entity. Every write records the previous state as a dataset version first, so a mistaken change can be rolled back from the version history.

**Why can't the agent publish a specification?** Publishing shares a specification with every user of the hub, so it is deliberately a human action in the web interface. An agent can build and validate a draft; you publish it.

## Building datasets

**The agent invents entity types that get rejected.** Only the entity types of the dataset's profile exist. The instructions the endpoint hands to every connected agent say to call `get_profile_schema` first; if a tool reports an unsupported type it lists the valid ones.

**Why does `create_dataset` refuse a name I deleted?** Deleted datasets are kept (soft delete), and names stay reserved by the deleted copy. Choose a different name.

**Which profile versions can I use?** `list_profiles` reports every profile with its versions — built-in standards and published specifications alike — and `get_profile_schema` and `create_dataset` require an exact version from that list.

## Building specifications

**The agent created entities but no hierarchy.** A specification is a tree: every entity except the root must be nested under a parent via a field whose type is `list` or `entity` and whose `items` names the child. The endpoint's instructions teach this, and `spec_validate` checks the draft builds — but an unlinked (orphaned) entity is not currently flagged, so check the preview before publishing.

**Where did my draft go?** Drafts persist in the hub database automatically per user — there is no separate save step over MCP. `list_spec_drafts` shows yours; a draft named like a colleague's is still only yours.

## Troubleshooting

**Calls suddenly return 421 or fail behind a proxy.** The endpoint pins the Host header to the deployment's own hostname (DNS-rebinding protection). Call it via its public URL, not by IP or an alternate name.

**A tool errored mid-change — is the dataset corrupted?** Writes snapshot the previous contents as a version before changing anything, and mutations are per entity. Check the dataset's version history in the web UI; the pre-change state is there.
