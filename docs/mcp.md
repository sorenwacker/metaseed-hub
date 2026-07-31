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
| `get_profile_relationships` | A profile's hierarchy: each entity's identifier, children, and cross-references |

### Editing entities

| Tool | |
| --- | --- |
| `create_entity` | Add one entity, without rewriting the dataset |
| `batch_create` | Add several entities in one call, root-first; one version is kept for the whole batch |
| `update_entity` | Change named fields; unnamed fields keep their values |
| `delete_entity` | Remove one entity |
| `list_entities` | The dataset's entities, with ids and values |
| `get_entity` | One entity's stored values |

Each of these reports what is still missing after the change, using the profile's own rules, so an agent can fill a dataset step by step instead of resending all of it. In a `batch_create` call, `parent_index` nests an item under an earlier item of the same batch, so a parent and its children can land together.

### Building a specification

| Tool | |
| --- | --- |
| `spec_create` | Start a new specification as a private draft |
| `spec_import_yaml` | Start a new private draft from a YAML specification document |
| `spec_clone` | Start a new private draft from a built-in profile or a published specification |
| `spec_add_entity` | Add an entity type |
| `spec_update_entity` | Change an entity's description or ontology term; unset arguments keep their values |
| `spec_rename_entity` | Rename an entity, cascading the root and every reference to it |
| `spec_delete_entity` | Remove an entity type |
| `spec_add_field` | Add a field to an entity. `items` links a `list` or `entity` field to the child entity it nests; `reference` and `parent_ref` name cross-references; `pattern`, `min_length`, `max_length`, `minimum`, `maximum`, `min_items`, `max_items`, and `enum` constrain its values |
| `spec_update_field` | Change a field's attributes in place, `items`, `reference`, and `parent_ref` among them, plus the eight constraints and `clear`; unset arguments keep their values |
| `spec_delete_field` | Remove a field |
| `spec_move_field` | Move a field one position up or down |
| `spec_add_rule` | Add a validation rule |
| `spec_update_rule` | Change a validation rule in place |
| `spec_delete_rule` | Remove a validation rule |
| `spec_set_root_entity` | Set the entity a dataset starts from |
| `spec_set_metadata` | Change the profile-level name, version, display name, description, or ontology |
| `spec_status` | A summary of the draft: name, version, root, entities, rules |
| `spec_validate` | What is wrong with the draft |
| `spec_preview_yaml` | The draft as YAML |
| `list_spec_drafts` | Your drafts |
| `spec_delete_draft` | Remove one of your own drafts |

A specification is a tree: every entity except the root must be linked under a parent by a field on the parent whose type is `list` or `entity` and whose `items` names the child. An unlinked entity is an orphan a dataset can never reach, and `spec_validate` does not flag orphans. The endpoint's instructions carry this workflow (shared with the standalone metaseed MCP server), so connected agents link entities as they build.

#### Editing constraints

`spec_update_field` accepts the same eight constraints as `spec_add_field` — `pattern`, `min_length`, `max_length`, `minimum`, `maximum`, `min_items`, `max_items`, `enum` — and merges them into the field's existing constraints. A constraint you do not supply keeps the value it had, so tightening `maximum` on a field that already has a `minimum` no longer discards the `minimum`.

Because an omitted argument means "unchanged", it cannot express removal. `clear` names the constraints to unset: `clear=["pattern"]` removes the pattern and leaves every other constraint alone. Setting and clearing the same constraint in one call is refused rather than resolved in some arbitrary order — the two requests contradict each other. The field is left untouched when a call is refused, including when a name is not one of the eight.

Adding a nested field with `items` also creates the parent's `identifier` field and a back-reference on the child, so the relationship is complete in both directions without further calls. `spec_delete_draft` removes a draft the caller owns; a draft a dataset is built on is not deleted, because the dataset would lose its specification.

Drafts are private to you. **Publishing is not available to an agent** — it shares a specification with every user of the hub, so it stays something you do yourself in the web interface.

Published specifications are included in `list_profiles`, because publishing shares a specification with every user of the hub — an agent can build a dataset against one it did not write.

### Ontology lookups

| Tool | |
| --- | --- |
| `search_ontology` | Search EMBL-EBI OLS4 for terms matching a query |
| `get_ontology_term` | One term's definition and synonyms, by CURIE id |
| `suggest_ontology_term` | Lighter suggestions for a partly typed term |
| `list_ontologies` | The ontologies OLS4 offers, with their ids |

These are read-only lookups against the [Ontology Lookup Service](https://www.ebi.ac.uk/ols4), through the same cached, rate-limited service the web interface uses. They require a valid token — an open endpoint would let anyone drive traffic through the hub — but touch no dataset.

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
