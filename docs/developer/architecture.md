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

The dead cookies are deleted when the issuer explicitly refuses the refresh token (`400`/`401` from the token endpoint), so a browser stops presenting a credential that has been discarded and each later page costs no doomed refresh call. Only then: an unreachable issuer is not a verdict on anyone's session, and clearing on an outage would sign every user out for its duration and leave them unable to sign back in. `RefreshResult.rejected` is what separates the two.

`next` is carried to the identity provider in the `metaseed_oauth_next` cookie rather than a query parameter, because the callback URL is registered with the provider and cannot vary. It is accepted only as a same-origin absolute path (`/hub/...`, no scheme, no `//` prefix), so it cannot become an open redirect; anything else falls back to `_post_login_landing`.

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
- **Mutate**: all writes go through the facade — `AppState.add_node`/`update_node`/`delete_node` (which keep the caches consistent) or `EntityService`, which wraps a `MetaseedClient` directly.
- **Save**: `save_dataset_state` serializes via `serialize_tree`, which delegates to `MetaseedClient.from_facade(state.facade).serialize(format="tree")` — the same serializer `EntityService` uses, so there is exactly one write format. `serialize_tree` refuses (raises) if the TreeNode cache holds nodes missing from the facade, because serializing would silently drop them.

A skipped node is data the hub stores but cannot show: it is absent from the loaded facade, so the next save drops it for good. It is therefore never discarded silently. `ensure_dataset_facade` logs every skip with the dataset id, and takes an optional `on_skip` callback so a caller can collect them; `ui/helpers/load_report.py` turns each `SkippedNode` into a validation issue (`rule: "unloadable_node"`) reported through the same two paths as spec drift — `_validation_report` (MCP) and `_render_validation_results` (web). Unlike drift, an unloadable node does set `valid: false`: the validator only ever saw the nodes that loaded, so reporting the dataset as valid would be an answer about a subset of it.

Reporting is not enough on the MCP write path, because an agent acts on the tool's return value and a successful edit reads as success. The `_editing` context therefore collects the skips and **refuses the edit** when there are any, before the tool body runs: the error names how many nodes did not load and their entity types, states that those nodes are in storage but not in the dataset as loaded, and names `save_dataset` as the way through for a caller that does intend to drop them (`unloadable_node_refusal` in `ui/helpers/load_report.py` builds the message). Every mutating tool routed through `_editing` inherits the refusal, so none of them can be the one that forgets. `save_dataset` is deliberately exempt: it replaces the whole dataset by definition, so dropping what did not load is the caller's stated intent rather than a side effect. The read tools are exempt too — `_loaded_client` collects nothing and only logs, because a read destroys nothing, and a damaged dataset must stay inspectable through `get_dataset`, `list_entities`, `get_entity`, and above all `validate_dataset`, which is where the `unloadable_node` report already lives.

The web save path (`save_dataset_state` after `ensure_dataset_facade`) carries the same exposure and does **not** refuse. Refusing there would make a dataset with an unloadable node completely uneditable in the browser: the UI has no whole-dataset replace action to serve as the deliberate override that `save_dataset` provides an agent, so the refusal would be a trap with no way out of it. What the web has instead is the validation panel, which names the unloadable nodes, and the version snapshot every changing save records. Making refusal safe on the web needs an explicit "drop them" affordance in the editor first; until that exists the honest state is reporting, not blocking.

Every save that changes `dataset.data` also records a `DatasetVersion` row (see the versions feature).

Dependency rule: routes and templates call hub helpers (`ui/helpers/`, `ui/services/`), and the helpers call metaseed's public API (`MetaseedClient`, `ProfileFacade`). Imports of metaseed's internal UI layer (`metaseed.ui`) are allowed only in the designated boundary module `ui/metaseed_ui.py`, which re-exports what the hub still uses: `AppState`/`TreeNode` (the request-scoped entity-tree cache derived from the facade) and the packaged template/static directories. The gate test `tests/test_metaseed_coupling.py` scans every module under `src/metaseed_hub` and fails on any other `metaseed.ui` import.

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
