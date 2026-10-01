# Architecture

## Tech Stack

| Layer | Technology |
|-------|------------|
| Backend | FastAPI + SQLAlchemy (async) |
| Database | PostgreSQL |
| Auth | Keycloak (OIDC) |
| Frontend | HTMX + Jinja2 |
| Real-time | WebSockets + Redis |

## Core Entities

```
Tenant (organization)
  └── Account (container)
        └── Project (profile: miappe, isa, dissco, dwc)
              └── Entities (Investigation, Study, etc.)
```

## Database Schema

Projects store entity data as JSONB:

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY,
    account_id UUID NOT NULL,
    name VARCHAR(255) NOT NULL,
    profile VARCHAR(100) NOT NULL,
    version VARCHAR(50) NOT NULL,
    data JSONB NOT NULL DEFAULT '{}'
);
```

## Sessions and expiry

A browser session is two cookies: `metaseed_access_token` (the OIDC access token, lifetime set by the issuer) and `metaseed_refresh_token` (30 days, or shorter if the issuer's session idles out first). `TokenRefreshMiddleware` verifies the access token on every request and, when it has expired, exchanges the refresh token for a new one and rewrites both cookies. A session therefore survives an expired access token silently, and ends only when the refresh fails.

When the refresh fails the session is over, and the hub says so in one shape everywhere:

- **A page request** answers `302` to `/hub/auth/login?next=<path>`.
- **An HTMX or `fetch` request** answers `401` with `HX-Redirect: /hub/auth/login?next=<path>`, which htmx acts on before it inspects the status. `hub.js` redirects on any `401` that carries no such header, so a route that forgets it still lands the user on the sign-in page instead of failing silently inside the page.

Both come from `AuthRequiredError`, raised by `require_user` and by every other authentication check. Raising a bare `HTTPException(401)` from a UI route is what produces a page that renders its chrome and then quietly fails to fill itself in; `tests/test_session_expiry.py` gates against it.

The identity provider's discovery document is fetched and cached in one place, `OIDCAuth.get_oidc_config` (`metaseed_hub.auth`), with a 10-second timeout and one mapping of every transport or status failure to a 503 that names the provider; the sign-in, callback, refresh and logout routes read it through `get_oidc_auth`. The route module used to keep a second implementation with its own cache and slightly different behaviour, and tests had to reset a module global to isolate it. `tests/test_one_oidc_discovery.py` fails when the route module fetches discovery itself.

A valid token is not proof that the account behind it still exists. An account is soft-deleted, not erased, and its membership rows stay in place, so every resolution of the caller's account treats a row with `deleted_at` set as absent: `access.live_user` is that lookup, shared by the access-ladder helpers (`verify_tenant_access`, `get_dataset_for_user`, the edit gate), the MCP layer and the token layer. `ensure_tenant_and_user`, which the home page, the spec builder, sharing and the auth routes resolve the account through, refuses a soft-deleted subject with `403` rather than serving them or re-provisioning a fresh account under the same subject: re-provisioning would silently resurrect the memberships, and the page it used to serve listed every shared dataset and draft and re-recorded the caller's collaborations. `tests/test_a_deleted_user_loses_shared_access.py` covers each path.

The dead cookies are deleted when the issuer explicitly refuses the refresh token (`400`/`401` from the token endpoint), so a browser stops presenting a credential that has been discarded and each later page costs no doomed refresh call. Only then: an unreachable issuer is not a verdict on anyone's session, and clearing on an outage would sign every user out for its duration and leave them unable to sign back in. `RefreshResult.rejected` is what separates the two.

`next` is carried to the identity provider in the `metaseed_oauth_next` cookie rather than a query parameter, because the callback URL is registered with the provider and cannot vary. It is accepted only as a same-origin absolute path (`/hub/...`, no scheme, no `//` prefix), so it cannot become an open redirect; anything else falls back to `_post_login_landing`.

Every state-changing POST validates the double-submit CSRF token (`validate_csrf_or_error` in `ui/security.py`, or `validate_csrf_token` behind it) before doing any work; the app-wide Origin guard is not a substitute, because it passes when the `Origin` header is absent. The SEEK routes were the one router that skipped it. `tests/test_csrf.py` names the routes that must answer 403 to a request without a token, and a new mutating route joins that list.

### Why authenticated pages are never cached

`NoStoreMiddleware` puts `Cache-Control: no-store` on every hub response. Without it the redirects above are unreachable: a browser serves history navigations — the back button, a restored tab, a reopened window — from its own cache without asking the server, so the last authenticated page keeps rendering after the session behind it has gone. What the user then sees is their dataset list, drawn from a snapshot, with every link on it leading to a sign-in page; and a dataset page restored the same way loads its panels through `hx-trigger="load"`, each of which now answers 401, leaving an empty editor. `no-store` also keeps the page out of the back/forward cache, so the browser re-asks and the redirect happens.

The header is set on responses from the hub app, which is where authenticated HTML is served. Static assets are mounted on the parent app in `main.py` and keep their own caching.

## Running locally

`make dev` starts the Postgres and Keycloak containers, migrates the database to head, and serves the hub with reload on port 7001. After migrating it runs `alembic check`, which compares the live schema with the models and stops the start if they differ. A database that reports the head revision but lacks a column the models declare, which is what a dump restored over a newer stamp produces, then fails at startup with the missing column named, rather than as an internal server error on whichever page first touches it. Repair such a database by hand with the statement the migration would have run, then start again.

## Dataset persistence

`dataset.data` (JSONB) stores a `{profile, version, spec_hash, tree: [...]}` envelope. The `{profile, version, tree}` part is produced by metaseed's `MetaseedClient.serialize(format="tree")`; each tree node carries `id`, `entity_type`, `label`, `data`, and `children`.

`spec_hash` is the hub's addition: `metaseed.specs.content_hash` of the profile spec the dataset was authored against, added by `stamp_spec_hash` on every write path (`save_dataset_state` and the MCP `_editing` context). It records provenance the `version` field cannot — a specification can be edited without its version changing, and two specs can declare the same version with different content. On load, `spec_drift_message` compares the stamp with the profile's current hash and reports a difference as a validation issue (`rule: "spec_drift"`) through both reporting paths: `_validation_report` (MCP) and `_render_validation_results` (web). Drift never blocks a load and never changes `valid`, which stays what metaseed's validator returned.

Envelopes written before the stamp existed have no `spec_hash`. A missing stamp means "unknown provenance", not "unchanged", so no drift is reported for it; the next save adds one.

The metaseed `ProfileFacade` is the single source of truth for entity data. `TreeNode`/`AppState` caches are derived views for template rendering; they are never written independently of the facade.

- **Load**: `ensure_dataset_facade` (ui/helpers/dataset_state.py) resolves the client for the dataset's schema (built-in profile, draft spec, or published spec) and populates the facade with `client.load(dataset.data, on_skip=...)`. Passing `on_skip` selects metaseed's permissive load: a node the profile cannot place — not a mapping, no `entity_type`, an entity type the schema does not define, or one whose creation fails — is dropped with its subtree instead of failing the whole load, and each drop is reported as a `metaseed.SkippedNode`. Entities that do load are reconstructed with `skip_validation`/`model_construct`, so incomplete drafts load without loss and legacy payloads with unknown field names load without failing. A node with no stored `id` gets a generated one and its children are loaded under it, so a missing id never flattens a subtree into roots. If stored entity data cannot be loaded at all, `DatasetDataLoadError` is raised instead of returning an empty state, because a later save from an empty state would overwrite the stored tree.
- **Mutate**: all writes go through the facade — `AppState.add_node`/`update_node`/`delete_node` (which keep the caches consistent) or `EntityService`, which wraps a `MetaseedClient` directly. Writes are permissive: a value is stored as the user entered it, even when the profile would reject it, and correctness is reported by validation rather than enforced at entry. Refusing at entry would discard what was typed and would make an incomplete draft unsaveable. Browser tree writes go through `add_entity_node`/`update_entity_node` in `ui/helpers/tree.py`, which pass `skip_validation=True`; `EntityService` and the MCP tools pass it to `MetaseedClient` themselves. The gate test `tests/test_writes_are_permissive.py` fails when `add_node`/`update_node` is called outside `ui/helpers/tree.py`, or when `create_entity`/`update_entity` is called without `skip_validation=True`: the six inline-table routes once called `update_node` directly, so a value the profile rejected — an ORCID entered as a URL — ended the request with a 500 and was not saved.
- **Save**: `save_dataset_state` serializes via `serialize_tree`, which delegates to `MetaseedClient.from_facade(state.facade).serialize(format="tree")` — the same serializer `EntityService` uses, so there is exactly one write format. The REST API writes the same form: `POST` and `PATCH /api/datasets` load the payload they were given (flat `entities` or a tree) into a facade and store `serialize_tree` of it, stamped with the specification hash, never the payload itself. They used to store the payload as sent, so a dataset pushed from a metaseed instance held a flat `entities` list the home cards counted as "No entities" and the drift check had no provenance for. The gate test `tests/test_api_stores_the_canonical_tree.py` fails when a row written through the API lacks `tree` or `spec_hash`. `serialize_tree` refuses (raises) if the TreeNode cache holds nodes missing from the facade, because serializing would silently drop them.

### Validation

The two reporting paths -- the web panel (`dataset_validate`) and the MCP `_validation_report` -- take their issues from one function, `validation_issues` in `ui/helpers/validation_report.py`, which runs metaseed's `MetaseedClient.validate()` over the loaded facade in a worker thread (`run_in_threadpool`, the way the archive importers run) and returns one record per issue with the node it belongs to. The web panel used to re-create each entity with Pydantic and report only what that raised: a wrong type, and nothing else -- not a missing required field, not a profile rule, not a reference to a study that does not exist -- while the MCP path asked metaseed, so an agent and a person disagreed about the same dataset. Running the validator on the request loop would hold every other request for the duration, which at thousands of entities is seconds. The gate test `tests/test_one_validation_report.py` fails when a client's `validate()` is called anywhere but that helper, or when either reporting path stops calling it; a second test feeds both paths the same dataset holding a malformed date, a missing required field and a broken reference, and requires each to report all three.

A skipped node is data the hub stores but cannot show: it is absent from the loaded facade, so the next save drops it for good. It is therefore never discarded silently. `ensure_dataset_facade` logs every skip with the dataset id, and takes an optional `on_skip` callback so a caller can collect them; `ui/helpers/load_report.py` turns each `SkippedNode` into a validation issue (`rule: "unloadable_node"`) reported through the same two paths as spec drift — `_validation_report` (MCP) and `_render_validation_results` (web). Unlike drift, an unloadable node does set `valid: false`: the validator only ever saw the nodes that loaded, so reporting the dataset as valid would be an answer about a subset of it.

Reporting is not enough on the MCP write path, because an agent acts on the tool's return value and a successful edit reads as success. The `_editing` context therefore collects the skips and **refuses the edit** when there are any, before the tool body runs: the error names how many nodes did not load and their entity types, states that those nodes are in storage but not in the dataset as loaded, and names `save_dataset` as the way through for a caller that does intend to drop them (`unloadable_node_refusal` in `ui/helpers/load_report.py` builds the message). Every mutating tool routed through `_editing` inherits the refusal, so none of them can be the one that forgets. `save_dataset` is deliberately exempt: it replaces the whole dataset by definition, so dropping what did not load is the caller's stated intent rather than a side effect. The read tools are exempt too — `_loaded_client` collects nothing and only logs, because a read destroys nothing, and a damaged dataset must stay inspectable through `get_dataset`, `list_entities`, `get_entity`, and above all `validate_dataset`, which is where the `unloadable_node` report already lives.

The web save path (`save_dataset_state` after `ensure_dataset_facade`) carries the same exposure and does **not** refuse. Refusing there would make a dataset with an unloadable node completely uneditable in the browser: the UI has no whole-dataset replace action to serve as the deliberate override that `save_dataset` provides an agent, so the refusal would be a trap with no way out of it. What the web has instead is the validation panel, which names the unloadable nodes, and the version snapshot every changing save records. Making refusal safe on the web needs an explicit "drop them" affordance in the editor first; until that exists the honest state is reporting, not blocking.

`save_dataset_state` is the only writer of `dataset.data`. Every path that changes a dataset loads what it wants stored into an `AppState` and hands it over: the browser routes through `ensure_dataset_facade_for_write`, the REST `PATCH` and a version restore through `load_payload_for_write` (the payload or the version's envelope, loaded into a transient copy of the row so nothing is stored that cannot be loaded), and the MCP `_editing` context and `save_dataset` tool through the same loader. The function serializes and stamps, records the new contents as a `DatasetVersion`, and first records the *previous* contents when no version row holds them, so a state written before this rule existed is never lost on the next save. Each of those paths used to write the column itself: `PATCH` without the row lock and without a version, restore with the version's envelope verbatim (unstamped, or in the legacy flat shape), and the MCP context with its own copy of the serialize-stamp-version sequence that had already diverged. The gate test `tests/test_one_dataset_save_path.py` fails when `dataset.data` is assigned, or `record_version` called, anywhere but `ui/helpers/dataset_state.py`.

### Concurrent edits

Every mutation is a read-modify-write of the whole stored tree: load, change in memory, serialize, write back. Two requests for one dataset that arrive together — a person adding a row while a colleague edits a cell, or an agent and the web UI — both loaded the same state, and the later write replaced the earlier one. Both also computed the next version number as `max + 1` from the same base, so the second insert failed on `uq_dataset_versions_number` and that request ended in a 500 with its edit lost (production, 260923). Writers on one dataset are therefore serialised on the dataset row. `lock_dataset_for_write` (ui/helpers/dataset_state.py) takes `SELECT ... FOR UPDATE` on the row and then re-reads it, so a second writer blocks until the first commits and loads what the first wrote; it is the same `FOR UPDATE` the spec builder takes on a draft's revision. `ensure_dataset_facade_for_write` takes the lock, so every browser write path has it — `get_dataset_state_for_mutation` for the table routes, `EntityService` for the form, the import and example routes — and so do the MCP `_editing` context and `save_dataset` tool and version restore, which take it directly. The lock is released when the request's session commits or closes. Reads never lock: the graph poll and validation would otherwise hold up every edit. The one exception is the REST API's payload check, which loads a transient row that is never stored; there is nothing to lock. Under the lock, `record_version` — the only place a `DatasetVersion` is constructed — numbers the next version `max + 1` safely; the three copies of that computation are gone. The gate test `tests/test_writes_to_a_dataset_are_serialised.py` fails when a function or class that saves a dataset (`save_dataset_state`, `record_version`) neither takes the lock (`lock_dataset_for_write`, or `ensure_dataset_facade_for_write`, which takes it) nor receives its state from a caller that did, and when `DatasetVersion` is constructed or `max(version_number)` computed outside `record_version`. A concurrency test against Postgres sends simultaneous edits to different fields of one dataset through the web loader and through `_editing`, and checks that every edit survives and the versions are numbered consecutively.

A specification draft is the same shape of problem: every edit rewrites the whole `SpecDraft.spec_data` column from an in-memory `SpecBuilderState`, and production runs two workers with a process-local `StateCache` each. `save_state_to_draft` (ui/spec_builder/access.py) is the only writer of that column. It reads and locks the row's `updated_at` with `FOR UPDATE` (`_stored_revision`), refuses with `DraftConflictError` when the row has moved since the caller's state was read, routes the name through `free_draft_name` so a name or version another draft holds cannot fail the save on `uq_spec_drafts_tenant_user_name_version`, and re-tags the cache at the new revision. Every path that changes a draft loads a state, mutates it and hands it there: the spec-builder routes through `DraftContext` and `load_state_for_draft`, which record the revision the state was read at; the reset route; the MCP `_building` context, which passes the `updated_at` it loaded the draft at as `expected_revision`; and `POST /api/specs` when it updates the caller's existing draft of a pushed specification. The MCP and REST writers, and the reset route, used to assign the column themselves, without the lock, the revision check or the name check, so an agent's edit that began before a browser save and committed after it discarded that save while both reported success, and a `spec_set_metadata` onto a version the account already held raised `IntegrityError`. The gate test `tests/test_one_draft_save_path.py` fails when `spec_data` is assigned on a draft anywhere but that module.

A dataset name is unique per tenant (`uq_datasets_tenant_name`). Every write that sets a name goes through `repositories/datasets.py`: `create_dataset` for the web form, file import, accession import, the MCP `create_dataset` tool and `POST /api/datasets`, and `rename_dataset` for `PATCH /api/datasets/{id}`. Both raise `DuplicateDatasetNameError` when the name is taken, saying whether a soft-deleted dataset holds it (it keeps its name, and the user no longer sees it), and each interface maps that one error to its own answer: the New Dataset form shows a message, the REST API returns 409, the MCP tool returns the message. The check is made by the database constraint, caught at the flush that inserts or updates the row, so two simultaneous requests for one name cannot both succeed. Each route used to create its own `Dataset` and catch `IntegrityError` around `commit()`, but `record_creator` flushes earlier, so the violation escaped as a 500; the REST paths did not catch it at all, and the MCP tool checked with a query beforehand, which a simultaneous request can pass. The gate test `tests/test_dataset_names_have_one_writer.py` fails when a `Dataset` is constructed with a `name`, or a dataset's `name` is assigned, outside that module; the payload check in `api/datasets.py` builds a nameless, never-persisted `Dataset` and is not a write.

A component depends on injected collaborators, never on ones it discovers: whoever composes the application supplies them. `ErrorRecordingMiddleware` (`errors.py`) takes the session factory and the caller resolver it records with as constructor arguments, which `ui/app.py` passes when it adds the middleware; it used to import the UI layer and the global database from inside its methods, a top-level module reaching down into the layers above it. Routes take their session as `DbSession`; `auth_callback` used to open one from the global `db`. The `WebSocketManager` is constructed in `create_app` with the Redis URL from settings and kept on `app.state.manager`, where the lifespan and the websocket endpoint find it; the module-level instance is gone, and a test substitutes the manager on the app instead of patching a module. The gate test `tests/test_collaborators_are_injected.py` fails when any of these reaches for a global again.

Dependency rule: routes and templates call hub helpers (`ui/helpers/`, `ui/services/`), and the helpers call metaseed's public API (`MetaseedClient`, `ProfileFacade`). Imports of metaseed's internal UI layer (`metaseed.ui`) are allowed only in the designated boundary module `ui/metaseed_ui.py`, which re-exports what the hub still uses: `AppState`/`TreeNode` (the request-scoped entity-tree cache derived from the facade) and the packaged template/static directories. The gate test `tests/test_metaseed_coupling.py` scans every module under `src/metaseed_hub` and fails on any other `metaseed.ui` import.

`ContentSecurityPolicyMiddleware` (`security_headers.py`) sends the same Content-Security-Policy that nginx sends in production, so a policy violation shows up in local and CI runs rather than only on the deployed site; `tests/test_the_csp_matches_production.py` compares the middleware's policy with the one in `ansible/roles/metaseed-hub/templates/nginx.conf.j2` and fails when they drift.

The stylesheet has two gates of its own in `tests/test_stylesheet.py`: no selector is defined twice at the top level of `hub.css` (the second definition used to win silently, by source order), and every class a template uses has a rule in the hub's or the library's stylesheet or is read by a script. Vulture cannot see CSS or templates; these are their vulture.

## Integration with metaseed

Metaseed Hub uses metaseed as a library through its public API:

```python
from metaseed import MetaseedClient, ProfileFacade
```

Key integrations:

| Service | Module | Purpose |
|---------|--------|---------|
| Entity data | `metaseed` (`MetaseedClient`, `ProfileFacade`) | Load, mutate, serialize entities |
| Entity forms | `metaseed.facade` | Dynamic form generation |
| Validation | `metaseed.validators` | Schema validation |
| Export | `metaseed_hub.ui.services.export` | Excel export over the facade API |
| Graph | `metaseed_hub.ui.services.graph` | Visualization data via `ProfileFacade.to_graph` |
| UI internals | `metaseed_hub.ui.metaseed_ui` | Sole boundary to `metaseed.ui` (`AppState`, assets) |

A profile the library loads is one object shared by every caller in the process (metaseed parses a `profile.yaml` once and caches the `ProfileSpec`), so the hub copies before it edits: `clone_spec` deep-copies the template it starts a draft from, and `_new_named_draft` deep-copies the specification an agent names before giving it that name. Editing the loaded object in place would rename or alter the built-in profile for every later request; `tests/test_mcp_spec_drafts.py` checks that creating a draft leaves the profile it came from untouched.

The hub never carries its own copy of a library function: a copy stays correct and stops improving, and every fix must land twice. Version strings are ordered with `metaseed.specs.versioning.version_sort_key` (four hub copies of a `MAJOR.MINOR` key once existed, one of them ordering non-numeric versions differently); a draft's status summary for agents is the library's, so the hub's MCP server and the standalone one report the same shape; renaming an entity in the spec builder goes through `SpecBuilder.rename_entity`, which also rewrites a validation rule's `reference` and keeps the entity's position, where the hand-written rename did neither. The gate test `tests/test_no_forked_code.py` fails when a hub module defines a function the library owns (`LIBRARY_FUNCTIONS`) or builds a workbook itself.

The same rule holds inside the hub: a rule written twice drifts. A draft or specification row is turned into a `SpecBuilderState` by one function, `state_of` in `ui/spec_builder/access.py`, which every reader (the spec-builder routes, the REST spec routes, the MCP tools) calls; `tests/test_one_draft_state_loader.py` fails on any other `SpecBuilderState.from_dict` call. The context every page needs — the CSRF token and its cookie, the version, the analytics settings, `base_url` — is filled by `standard_context` in `ui/render.py`, which `render_template`, the spec builder's `render_with_context` and the explorer's renderer all use; the two copies used to omit `base_url`, so every spec-builder and explorer page emitted the template's hard-coded production URL on a local instance (`tests/test_one_render_path.py`). The column a child row inherits from its parent is named by `parent_reference_field` alone; the table renderer and the row route both call it (`tests/test_one_parent_reference_rule.py`).
