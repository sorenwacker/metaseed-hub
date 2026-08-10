# Codebase Review — metaseed-hub

Date: 260730. Scope: all 69 Python source files under `src/metaseed_hub/` (~15k LOC), reviewed by a 13-group multi-agent workflow; every high/medium finding was independently re-verified by an adversarial pass that could refute it or correct its severity. Severities below are the corrected ones.

## Baseline gates

| Gate | Command | Result |
|---|---|---|
| Lint | `uv run ruff check src tests` | pass |
| Types | `uv run mypy src` (strict per pre-commit) | pass, 64 files |
| Dead code | `uv run vulture src/ vulture_whitelist.py --min-confidence=80` | pass |
| Security | `uv run bandit -r src/metaseed_hub --severity-level medium` | pass |
| Tests | `uv run pytest -m "not selenium"` | 491 passed, 1 skipped |
| File size | limit 1000 LOC | pass, largest file 856 LOC |

## Summary

118 raw findings; 55 confirmed (8 high, 30 medium, 17 low), 0 refuted, 63 unverified low-confidence notes (appendix).

Confirmed findings by category: correctness 36, consistency 9, dead-code 6, design 2, docstring 2.

Recurring themes:

- The dataset storage format is split between a legacy flat representation and the current tree representation; the MCP server and `DatabaseDatasetRepository` still read/write the flat form, so MCP silently sees tree-format datasets as empty and can overwrite them.
- Enum coercion from form input (`ReactionType(...)`, `SpecDraftRole(...)`, `float()`/`int()` on rule fields) is unguarded in several routes, turning malformed input into 500s.
- Several failure paths swallow errors or leak internals: raw exception text and tracebacks are interpolated into HTML fragments (one confirmed reflected XSS), and load failures are silently treated as empty state.
- Authorization is declared but not enforced in places: spec-draft roles are stored yet never checked on mutating routes; comment `parent_id` is not scope-checked.
- Dead or unreachable code persists behind the vulture whitelist instead of being removed (unused repository class, unused websocket send path, unreachable config fallbacks).

## High severity (8)

### H1. list_entities and get_entity read only the flat format and see tree-format datasets as empty

`src/metaseed_hub/mcp/__init__.py:647` — correctness

Both tools read `(row.data or {}).get("entities", [])` and the `_node_id`/`_type` keys, which exist only in metaseed's flat serialization. Datasets built or edited through the web UI are stored in tree format ({profile, version, tree: [...]} with id/entity_type/data/children), so `list_entities` returns an empty list and `get_entity` raises "No entity ... " for datasets that plainly contain entities — while create_entity/update_entity/delete_entity on the very same dataset work, because _editing goes through ensure_dataset_facade -> client.load(), which auto-detects both formats. An agent pointed at a user's existing (web-created) dataset is told it is empty.

**Fix:** Read through the facade like the editing tools do (ensure_dataset_facade + client.serialize()/facade.to_dict() in memory), or explicitly handle both the "entities" and "tree" envelopes, and cover the tree-format case in tests/test_mcp_endpoint.py.

### H2. Dict mutated during async iteration over room connections

`src/metaseed_hub/websocket/__init__.py:152` — correctness

Both `_dispatch_local` (line 152: `for conn_id, connection in room.connections.items():`) and the no-Redis path of `broadcast_to_room` (line 295, same pattern) iterate the live `room.connections` dict while awaiting `connection.websocket.send_text(message_json)` inside the loop. Every send is a suspension point; if a concurrent `join_room` or `leave_room` for the same room runs during that await (routine under load, and `leave_room` is even called from within these methods' own cleanup on other rooms), the dict changes size mid-iteration and raises `RuntimeError: dictionary changed size during iteration`. In `_dispatch_local` this is swallowed by `_listen`'s catch-and-log, silently dropping delivery to all remaining recipients of that message; in `broadcast_to_room` it propagates into `handle_connection`'s generic handler and tears down the sender's connection.

**Fix:** Iterate over a snapshot: `for conn_id, connection in list(room.connections.items()):` in both `_dispatch_local` and `broadcast_to_room`.

### H3. Reflected XSS: EntityServiceError.user_message rendered into HTML without escaping

`src/metaseed_hub/ui/routes/entity.py:45` — correctness

Multiple handlers return HTMLResponse(f"<div class='error'>{e.user_message}</div>") (lines 45, 50, 101, 190, 232, 243, 280) and validation errors as f"<li>{error}</li>" (line 212), all unescaped. user_message embeds user-controlled input: services/entity_service.py line 253 builds f"Unknown entity type: {entity_type}..." where entity_type comes from the URL path in GET /datasets/{dataset_id}/form/{entity_type} (dataset_entity_form) and from form data in the POST handlers. A crafted GET link like /form/%3Cscript%3E... reflects attacker HTML with no CSRF barrier. Line 164 of the service also interpolates raw exception text. Contrast with admin.py, which carefully html.escape()s every message it echoes, and table.py which escapes cell values.

**Fix:** html.escape() e.user_message, result.error_message, and each validation error before embedding in HTMLResponse, or render these snippets through a Jinja partial (auto-escaping) like the rest of the file does for forms.

### H4. dataset_import silently drops JSON/YAML files with an 'entities' list

`src/metaseed_hub/ui/routes/dataset/crud.py:309` — correctness

In dataset_import, `entities_data = data.get("entities", []) if isinstance(data, dict) else []` is computed and then only checked for falsiness (`if not entities_data and isinstance(data, dict):` uses the data as a single root entity). When `entities_data` IS non-empty — i.e. the file is in the export/serialize format `{"entities": [...]}` — no branch processes it: the entity list is never iterated and the dataset is created empty with no error shown (the redirect to the new dataset succeeds). The comment '# JSON/YAML import - use existing logic' suggests the handling was lost in a refactor. The sibling route dataset_import_into_existing in editor.py (lines 607-629) does handle this exact shape by iterating data["entities"] with per-entity `_type`.

**Fix:** Iterate entities_data the same way editor.py's dataset_import_into_existing does (group by `_type`, root entity first, call add_entity_node per entity), or extract that logic into a shared helper used by both import routes.

### H5. add_dataset_comment does not validate parent_id scope or existence

`src/metaseed_hub/ui/routes/dataset/comments.py:129` — correctness

`parent_id=parent_id if parent_id else None` is taken directly from the form. (a) A parent comment belonging to a different dataset can be referenced, so a user with access only to dataset A can attach a reply under a comment thread in dataset B (the reply renders in B's thread via the Comment.replies relationship). This is exactly the cross-dataset scoping hole the same file explicitly fixed for delete (line 164 comment) and react (line 200 comment). (b) A nonexistent parent_id raises a foreign-key IntegrityError on commit, producing an unhandled 500.

**Fix:** When parent_id is supplied, select the parent with `Comment.id == parent_id, Comment.dataset_id == dataset_id` and return a 400/404 fragment if absent, mirroring the scoped lookups in delete_dataset_comment and react_to_comment.

### H6. ensure_state() ignores Dataset.spec_id, breaking entity CRUD for datasets built from published specs

`src/metaseed_hub/ui/services/entity_service.py:125` — correctness

ensure_state() branches only on `if self._dataset.spec_draft_id:` else `self._load_builtin_client()`. But Dataset has a third provenance: `spec_id` (published spec, models/__init__.py:228), and crud.py dataset_create stores `spec_id=...`, `spec_draft_id=None`, `profile=published.name.lower()` for such datasets. The sibling loader `ensure_dataset_facade` (ui/helpers/dataset_state.py:89-106) explicitly handles this case via `MetaseedClient.from_spec(published.spec_data)`. EntityService instead calls `MetaseedClient(self._dataset.profile, ...)`, which raises ProfileNotFoundError for any non-built-in spec name (or loads the wrong schema on a name collision). All entity routes (ui/routes/entity.py) go through EntityService, so entity forms and create/update/delete fail with "Could not load profile '<spec name>'" for every dataset created from a published spec. tests/test_dataset_from_published_spec.py only exercises ensure_dataset_facade, so the gates cannot catch this.

**Fix:** Add a spec_id branch in ensure_state() mirroring ensure_dataset_facade: load the Spec row, unwrap the optional "spec" key, and build the client with MetaseedClient.from_spec(); raise SpecNotFoundError when the row or spec_data is missing. Add a test creating/updating an entity on a spec_id-backed dataset.

### H7. _load_tree_data swallows load failures, enabling silent data loss on next save

`src/metaseed_hub/ui/services/entity_service.py:227` — correctness

`except Exception as e: logger.error(f"Failed to load tree data: {e}")` returns normally, so ensure_state() succeeds with an empty client even though the dataset has stored data. Any subsequent create_or_update_entity()/delete_entity() then calls _save_state(), which serializes the near-empty client and executes `self._dataset.data = tree_data` — overwriting the entire stored entity tree with just the newly added entity. A transient or format-related deserialization failure thus destroys the dataset contents without any user-visible error (a DatasetVersion of the truncated data is even recorded).

**Fix:** Raise FacadeLoadError (with a user_message) from _load_tree_data instead of logging and continuing, so ensure_state() fails and no save can overwrite data that was never loaded.

### H8. SpecDraftRole (viewer/editor/owner) is assigned but never enforced on any mutating route

`src/metaseed_hub/ui/spec_builder/routes/member_routes.py:113` — correctness

Members are added with role=SpecDraftRole.VIEWER and owners can change roles via update_spec_member_role, but no route or access helper ever checks the role when authorizing writes. All mutating draft routes (entity/field/rule CRUD via DraftContextDep, plus reset_draft and publish_draft in draft_routes.py which use load_state_for_draft) gate only on _user_can_access_draft, which returns True for ANY SpecDraftMember row regardless of role. A grep for SpecDraftRole across src/ confirms the only uses are assignment (line 113), parsing (line 142), and the owner-transfer query (line 194) — nothing enforces it. Consequence: a member explicitly shared as VIEWER can add/delete entities and fields, reset the draft to empty, and even publish the draft (which deletes the draft and creates a published Spec). The role field is aspirational data: the UI presents a viewer/editor distinction that the backend does not implement.

**Fix:** Enforce role in the access layer: make get_draft_context (and load_state_for_draft when called from mutating routes) require EDITOR or OWNER for writes, keeping VIEWER read-only, and restrict publish/reset to the draft owner or OWNER-role members. Add tests for the viewer-cannot-edit behavior.

## Medium severity (30)

### M1. Legacy Keycloak fallback is unreachable because oidc_* defaults are non-empty

`src/metaseed_hub/config.py:65` — correctness

The keycloak_url/keycloak_realm/keycloak_client_id/keycloak_client_secret fields exist "for backwards compatibility", but the fallback branches can never trigger under normal configuration: `effective_issuer` checks `if self.oidc_issuer:` first, and `oidc_issuer` defaults to "http://localhost:7080/realms/metaseed" (likewise `oidc_client_id` defaults to "metaseed-hub" and `oidc_client_secret` to "metaseed-hub-dev-secret"). A deployment that sets only the KEYCLOAK_* environment variables therefore silently authenticates against the localhost dev issuer with dev credentials instead of its Keycloak server. The compatibility only works if the operator also explicitly blanks OIDC_ISSUER/OIDC_CLIENT_ID/OIDC_CLIENT_SECRET, which nothing documents. Grep confirms the keycloak_* fields are referenced nowhere outside these three properties.

**Fix:** Either make the oidc_* defaults empty and centralize the dev defaults in effective_* (so legacy config actually wins when oidc_* is unset), or delete the keycloak_* fields and the fallback branches if legacy deployments no longer exist.

### M2. Comment.replies / SpecComment.replies: ORM delete contradicts FK ON DELETE CASCADE

`src/metaseed_hub/models/__init__.py:505` — correctness

parent_id has ForeignKey(..., ondelete="CASCADE"), but the `replies` relationship (Comment line 505, SpecComment line 589) has neither passive_deletes=True nor cascade="all, delete-orphan". Both comment routes hard-delete via `await session.delete(comment)` (src/metaseed_hub/ui/routes/dataset/comments.py:170, src/metaseed_hub/ui/spec_builder/routes/comment_routes.py:137). At flush, SQLAlchemy loads the replies and issues UPDATE ... SET parent_id = NULL before deleting the parent, so the DB CASCADE never fires: replies silently become top-level comments instead of being removed with the thread. The User model documents exactly this pitfall and uses passive_deletes for its child relationships, so the schema intent here diverges from the ORM behavior. No test covers deleting a comment that has replies, so the divergence is unpinned.

**Fix:** Decide the intended behavior. If replies should die with the parent (what the DDL says), add passive_deletes=True to Comment.replies and SpecComment.replies and add a test. If replies should be promoted to top-level, change the FK to ondelete="SET NULL" so DDL and ORM agree.

### M3. DatabaseDatasetRepository is never used by production code

`src/metaseed_hub/repositories/dataset.py:89` — dead-code

Grep across the whole source tree shows `DatabaseDatasetRepository` is referenced only in `repositories/__init__.py` (export) and `tests/test_repository_dataset.py`. No production module (api/, ui/, mcp/) instantiates it: `api/datasets.py` persists `Dataset` rows directly via the session and only borrows the static `AsyncDatasetRepository.validate_name`, and the UI/MCP paths also operate on the model directly. The upstream base-class comment in metaseed claims "metaseed-hub both implements and calls it", but the hub only implements it. Vulture cannot catch this because the tests exercise the class. The class, its two module-level helpers `_tree_to_entities`/`_entities_to_tree`, and its test file exist without any application code path.

**Fix:** Either wire the repository into the API/UI dataset persistence paths (which would also centralize the duplicated tree/name-validation logic) or remove the class and its tests. If it is intentionally kept as the future storage seam, document that in the module docstring.

### M4. _entities_to_tree silently reparents children that precede their parent

`src/metaseed_hub/repositories/dataset.py:79` — correctness

The tree is built in a single pass: `if parent_unique_id and parent_unique_id in nodes_by_unique_id:` attaches a child only when the parent has already been seen; otherwise the node is appended to `roots` and its `_parent_unique_id` is silently discarded. `_tree_to_entities` happens to emit pre-order (parent first), so DB round trips survive, but `save()` accepts arbitrary `DatasetData.entities` from callers, and nothing in the `AsyncDatasetRepository` contract requires parent-before-child ordering. A child listed before its parent is quietly promoted to a root, corrupting the hierarchy with no error.

**Fix:** Build the tree in two passes: first create all nodes keyed by unique_id, then link children to parents; alternatively raise on an unresolvable `_parent_unique_id` instead of silently dropping it.

### M5. _editing persists flat serialization while the hub stores datasets in tree format

`src/metaseed_hub/mcp/__init__.py:225` — correctness

`data = client.serialize()` uses metaseed's default flat format ({profile, version, entities: [...]} with _type/_node_id keys). The hub's canonical stored format for dataset.data is the tree envelope: EntityService._save_to_db writes `self._client.serialize(format="tree")` (ui/services/entity_service.py:498), serialize_tree's docstring says the two formats "MUST stay format-compatible" with the tree envelope, and tree-only readers exist: ui/routes/dataset/versions.py reads `data.get("tree", [])` for the version diff view, repositories/dataset.py reads `data.get("tree", [])`, and deserialize_tree/get_dataset_state bail out when "tree" is absent. After any MCP create_entity/update_entity/delete_entity call the stored dataset loses its "tree" key, so the web UI's version diff shows empty trees and every data["tree"] consumer silently sees an empty dataset. The MCP tests never catch this because they create and read data exclusively through MCP (flat both ways).

**Fix:** Persist `client.serialize(format="tree")` in _editing so MCP writes stay format-compatible with EntityService, and add a round-trip test that edits a web-UI (tree-format) dataset through an MCP tool and then reads it back through deserialize_tree/versions diff.

### M6. create_dataset collides with soft-deleted rows and surfaces an unhandled IntegrityError

`src/metaseed_hub/mcp/__init__.py:394` — correctness

The duplicate check filters `Dataset.deleted_at.is_(None)`, but `uq_datasets_tenant_name` is a full (non-partial) unique constraint on (tenant_id, name) — confirmed in alembic/versions/260601_remove_accounts.py. The module's own delete_dataset is soft (`dataset.soft_delete()`), so the natural agent flow delete_dataset("x") -> create_dataset("x", ...) passes the existence check and then fails at commit with an asyncpg IntegrityError instead of the friendly ValueError, leaving the agent with an opaque internal error it cannot act on.

**Fix:** Either include soft-deleted rows in the duplicate check (with an error message explaining the name is held by a deleted dataset), or catch IntegrityError around the commit and re-raise as ValueError; longer-term make the unique constraint partial on deleted_at IS NULL.

### M7. Published specs are advertised by list_profiles but unusable by every other tool

`src/metaseed_hub/mcp/__init__.py:520` — correctness

list_profiles returns published Spec rows and its docstring says "any of them can be used here", and the server instructions tell the agent to call get_profile_schema for the profile it is using. But get_profile_schema loads only filesystem YAML via `SpecLoader(profile=profile).load_profile(...)`, so a published spec name raises SpecLoadError; and create_dataset never sets spec_id, so a dataset created against a published profile falls into ensure_dataset_facade's built-in branch (`MetaseedClient(dataset.profile, dataset.version)`), which fails — facade is None, and _editing/_validation_report answer "The profile ... could not be loaded ... Confirm it exists with list_profiles", which is circular advice because list_profiles does list it.

**Fix:** Resolve published specs from the Spec table in get_profile_schema (MetaseedClient.from_spec on spec_data, as ensure_dataset_facade does), and have create_dataset set spec_id when the profile matches a published spec; or stop advertising published specs as usable from this endpoint.

### M8. _caller is a raw async generator, deferring session close and forcing 17 unreachable raises

`src/metaseed_hub/mcp/__init__.py:73` — design

Every tool consumes _caller via `async for session, user in _caller(): ... return ...` and ends with `raise NotAuthenticatedError("unreachable")`. Returning (or raising) from inside the loop abandons the generator while it is suspended inside `async with db.session_factory() as session:`; the session's __aexit__ only runs when the event loop's async-generator finalization hooks later aclose() the abandoned generator, so DB sessions/connections are released non-deterministically after the request instead of at the end of the tool body. It is also inconsistent with the module's own _editing and _building, which are @asynccontextmanager, and it forces the boilerplate unreachable raise in all 17 tools.

**Fix:** Make _caller an @asynccontextmanager and use `async with _caller() as (session, user):` in every tool; this closes the session deterministically and deletes the 17 `raise NotAuthenticatedError("unreachable")` lines.

### M9. Draft lookup is tenant-scoped while SpecDraft names are unique per user

`src/metaseed_hub/mcp/__init__.py:241` — correctness

_owned_draft selects `SpecDraft.tenant_id == user.tenant_id, SpecDraft.name == name` and calls scalar_one_or_none(), but the model's constraint is UniqueConstraint("tenant_id", "user_id", "name") — "each with a unique name per user" per the model docstring. In a tenant holding more than one user, two same-named drafts make scalar_one_or_none() raise an unhandled MultipleResultsFound, spec_create refuses a name merely because another user holds it, and users can edit each other's drafts even though spec_create's docstring promises "A draft is visible only to you". This is currently masked because ensure_tenant_and_user provisions one tenant per keycloak user, so the module's isolation guarantee silently depends on that provisioning detail.

**Fix:** Add `SpecDraft.user_id == user.id` to the filters in _owned_draft, spec_create's duplicate check, and list_spec_drafts, matching the model's uniqueness scope.

### M10. list_profiles gives no version for built-in profiles although every follow-up tool requires one

`src/metaseed_hub/mcp/__init__.py:492` — design

`SpecLoader().list_profiles()` returns bare names (list[str], e.g. ["miappe", "isa"]), so the tool returns {"built_in": ["miappe", ...], "published": [{"name": ..., "version": ..., "source": ...}]} — two different element shapes in one response — and the built-in entries carry no version. Yet get_profile_schema(profile, version) and create_dataset(name, profile, version) both require an exact version, and the server instructions direct the agent straight from list_profiles to get_profile_schema. The agent has no tool that reveals which versions exist for a built-in profile and must guess (the docstring example "1.0" is wrong for miappe, which ships 1.1/1.2).

**Fix:** Return built-in profiles as {name, versions (or version), source: "built_in"} objects, enumerating available versions from the loader, so both lists share a shape and the version needed by the other tools is discoverable.

### M11. connection_id based on id() is not unique across instances but is used cluster-wide

`src/metaseed_hub/websocket/__init__.py:348` — correctness

`connection_id = f"{user_id}:{id(websocket)}"` is only unique within a single process (CPython `id()` is a memory address). The ID is embedded as `exclude` in the Redis envelope (`broadcast_to_room` line 284) and applied by every instance's `_dispatch_local` (line 153: `if conn_id == exclude_connection: continue`). In the multi-instance deployment this Redis fan-out exists to support, the same user connected on two instances can coincidentally get identical `id()` values, causing the other instance to wrongly exclude that connection and silently drop messages for it.

**Fix:** Generate globally unique connection IDs, e.g. `connection_id = f"{user_id}:{uuid4().hex}"`.

### M12. send_to_connection is never called; suppressed via vulture whitelist instead of removed

`src/metaseed_hub/websocket/__init__.py:307` — dead-code

Grep across the entire repository (src, tests, docs, templates) finds no caller of `send_to_connection` — the only references are its definition (line 307) and an entry in `vulture_whitelist.py:96` (`send_to_connection  # noqa: F821`). The dead-code gate flagged it and it was whitelisted rather than deleted, which contradicts the project rule to remove dead code. It also has zero test coverage, so its error path (leave-on-send-failure) is unverified.

**Fix:** Delete `send_to_connection` and its `vulture_whitelist.py` entry, or add a real caller plus tests if the capability is actually required.

### M13. Naive datetime.now() diverges from the codebase-wide datetime.now(UTC) convention

`src/metaseed_hub/websocket/__init__.py:360` — consistency

`message["timestamp"] = datetime.now().isoformat()` (line 360) and `connected_at: datetime = field(default_factory=datetime.now)` (line 25) produce naive local-time values. Every other module (backup.py, tokens.py, errors.py, models/mixins.py, models/__init__.py, ui/routes/admin.py) uses timezone-aware `datetime.now(UTC)`. The resulting ISO strings sent to websocket clients (`timestamp`, presence `connected_at`) carry no UTC offset, so clients in other timezones cannot interpret them correctly, and the values are inconsistent with timestamps served by the HTTP side.

**Fix:** Use `datetime.now(UTC)` in both places (`from datetime import UTC, datetime`; `field(default_factory=lambda: datetime.now(UTC))`).

### M14. Blocking HTTP call inside async request rendering

`src/metaseed_hub/ui/render.py:41` — correctness

get_repo_stars() performs a synchronous `httpx.get(..., timeout=2.0)` and is registered as a Jinja global (app.py:196) called from base.html's footer (`{% set repo_stars = get_repo_stars() %}`) on every page. Template rendering happens inside async endpoints on the event loop, so whenever the cache entry is expired (hourly, or every 5 minutes after a failure) one request stalls the entire event loop for up to 2 seconds, freezing all concurrent requests. Every other network call in this codebase is async.

**Fix:** Fetch the star count asynchronously outside the render path (e.g. a background task refreshing the cache, or run the fetch via anyio.to_thread), and have the template global only read the cached value.

### M15. spec_to_yaml mutates global yaml module state on every call

`src/metaseed_hub/ui/spec_builder_helpers.py:109` — correctness

`yaml.add_representer(str, str_representer)` registers the custom string representer on the process-global default `yaml.Dumper`. After the first call to spec_to_yaml, every `yaml.dump` anywhere in the process (including metaseed library code and third-party code) silently switches multi-line strings to block style. The registration is also redundantly repeated on each call. This is a hidden global side effect from what looks like a pure conversion function.

**Fix:** Define a module-level `class _SpecDumper(yaml.Dumper)` and register the representer on it once at import time, then pass `Dumper=_SpecDumper` to `yaml.dump`.

### M16. Published specs omitted from explorer catalog for users without a tenant row

`src/metaseed_hub/ui/explore_routes.py:228` — correctness

In _build_explore_catalog the published-specs query (`select(Spec).where(Spec.status == SpecStatus.PUBLISHED, ...)`) is nested inside `if tenant:` even though it is not tenant-scoped. A logged-in user whose Tenant row does not exist yet (e.g. they navigate to /explore before visiting the home page, which is where ensure_tenant_and_user runs) gets a catalog with no published specs, while load_profile_spec (line 110) deliberately treats published specs as readable by anyone. The gating contradicts the documented publishing semantics and depends on incidental page-visit order.

**Fix:** Move the published-specs query out of the `if tenant:` block; only the drafts query is tenant-dependent.

### M17. Token-exchange request in auth_callback has no error handling and no explicit timeout

`src/metaseed_hub/ui/routes/auth.py:154` — correctness

auth_callback wraps every failure mode in redirect-with-error (?error=auth_failed, invalid_state, no_code, token_exchange_failed), but the httpx.AsyncClient().post() to the token endpoint (lines 154-164) is not inside try/except: httpx.ConnectError/TimeoutException during an IdP blip escapes as an unhandled 500 to a user mid sign-in, instead of the redirect the function otherwise guarantees. It also omits the explicit timeout that the sibling refresh_access_token sets (timeout=10.0, line 246).

**Fix:** Wrap the POST in try/except httpx.HTTPError and redirect to /hub/?error=token_exchange_failed on failure; pass timeout=10.0 for consistency with refresh_access_token.

### M18. get_oidc_config only catches ConnectError/HTTPStatusError; timeouts escape as 500

`src/metaseed_hub/ui/routes/auth.py:57` — correctness

The function's docstring says it raises HTTPException when the 'OIDC provider is unreachable', and it converts httpx.ConnectError and httpx.HTTPStatusError to 503. But httpx.TimeoutException (ReadTimeout, ConnectTimeout under some conditions) and other TransportErrors are not caught, so a slow/hanging provider produces an unhandled 500 instead of the documented 503 in auth_login, auth_callback, and auth_logout.

**Fix:** Catch httpx.HTTPError (the common base) or add httpx.TimeoutException/httpx.TransportError to the except clauses, mapping to the same 503 detail.

### M19. update_table_cell silently ignores empty values, making cells impossible to clear

`src/metaseed_hub/ui/routes/table.py:441` — correctness

In update_table_cell, `if raw_value is None or raw_value == "": continue` skips empty submissions. The cell inputs post on change/blur, so a user who deletes a cell's content sees an empty cell (and gets HX-Trigger entityChanged / HTTP 200) while the server keeps the old value; the stored data silently diverges from what the UI shows until the next full re-render. By contrast, update_primitive_list_item accepts an empty string and stores it.

**Fix:** Treat an empty submission as clearing the field (pop it from current_values or set it to None) instead of skipping it, or return a non-success response so the UI restores the previous value.

### M20. Entity type defaults to the profile name, which is never a valid entity type

`src/metaseed_hub/ui/routes/dataset/editor.py:609` — correctness

In dataset_import_into_existing, `etype = entity.get("_type", dataset.profile)` (also line 626 for JSON) defaults an entity's type to the profile name (e.g. "miappe"). Profile names are not entity types, so every entity lacking `_type` is grouped under a type that is not in `facade.entities`; since the processing loop iterates only `entity_order` built from facade.entities, these entities are silently skipped — they are neither imported nor counted in the errors list.

**Fix:** Default untyped entities to the profile's root entity type (already computed as `root_entity` further down; hoist that lookup before parsing), or reject entities without `_type` with an explicit per-entity error.

### M21. Raw exception text interpolated unescaped into HTML fragment

`src/metaseed_hub/ui/routes/dataset/editor.py:649` — correctness

`return HTMLResponse(f"<div class='notification error'>Parse error: {e}</div>", ...)` inserts the exception message without html.escape(). YAML/JSON parse exceptions embed excerpts of the uploaded file, so file content is reflected into the DOM of the HTMX swap target unescaped (markup/script injection; reflected to the uploader). The sibling error path in crud.py, `_import_failure_message`, carefully html.escape()s both the detail and the user value — this route diverges from that established pattern.

**Fix:** Escape the exception text with html.escape() before interpolating, as _import_failure_message in crud.py does.

### M22. dataset_graph_api accepts and documents node_id but never uses it

`src/metaseed_hub/ui/routes/dataset/editor.py:408` — docstring

The docstring promises 'If provided, only include this node and its descendants', but the implementation calls `build_graph(state)` — verified in metaseed/ui/services/graph.py the signature is `build_graph(state: AppState)` with no node filter — so the parameter is silently ignored and the full graph is always returned. The dataset_graph page route (line 381) legitimately passes node_id to the template, so the API presumably receives it from the frontend and returns unfiltered data.

**Fix:** Either implement descendant filtering (filter nodes/edges from build_graph output by node_id) or remove the parameter and the docstring claim.

### M23. Accession-import versions are saved without an author

`src/metaseed_hub/ui/routes/dataset/crud.py:406` — consistency

create_dataset_from_accession calls `await save_dataset_state(session, dataset, state)` without the user argument, so the DatasetVersion created for an accession import has created_by_id=None. Every other save path in this module passes `user` (lines 326, 504, 665, 792), and the sole caller dataset_import_accession has the acting user available. save_dataset_state's docstring says the user-less form is for 'background/non-request callers', which this is not.

**Fix:** Add a `user: TokenUser | None` parameter to create_dataset_from_accession and pass it through from dataset_import_accession to save_dataset_state.

### M24. Stale node_id on update silently creates a duplicate entity instead of failing

`src/metaseed_hub/ui/services/entity_service.py:306` — correctness

`if node_id and node_id in state.nodes_by_id:` routes unknown node_ids into the create branch, where `node_id = entity.id` silently replaces the caller's id. If the browser form holds a stale node_id (entity deleted by another user/tab between form render and submit), the intended update becomes a brand-new entity duplicating the data, with no indication anything was wrong. delete_entity() handles the same situation explicitly with `error_message="Entity not found."`, so the two methods disagree on how a missing node is treated.

**Fix:** When node_id is provided but absent from state.nodes_by_id, return EntitySaveResult(success=False, error_message="Entity not found. It may have been deleted.") instead of falling through to create.

### M25. _save_state duplicates save_dataset_state version/persist logic

`src/metaseed_hub/ui/services/entity_service.py:484` — consistency

_save_state re-implements ui/helpers/dataset_state.py save_dataset_state almost line for line: the max(version_number) query, the created_by_id resolution via User.keycloak_id, the `if tree_data != self._dataset.data` guard, DatasetVersion construction, flag_modified, add, commit. The copies have already diverged: save_dataset_state resolves created_by_id unconditionally and calls `await session.refresh(dataset)` after commit; _save_state resolves the user only inside the changed-data branch and never refreshes. The in-file comment even acknowledges the coupling ("matching save_dataset_state and the restore path"). Future fixes (e.g. to the version-number race between the SELECT max() and INSERT) must be applied twice.

**Fix:** Extract the versioning+persist step into one shared helper (e.g. accept the serialized tree dict) and call it from both _save_state and save_dataset_state.

### M26. _user_can_access_draft and require_draft_access docstrings omit the tenant-wide access grant

`src/metaseed_hub/ui/spec_builder/access.py:155` — docstring

`_user_can_access_draft` is documented as 'Check if user can access a draft (owner or member)' with Returns 'True if user is the owner or a member of the draft', and `require_draft_access` (line 220) says 'requiring the caller to be its owner or a member'. But lines 180-187 additionally grant access to ANY user in the same tenant (`User.tenant_id == draft.tenant_id`). For an access-control function this is a materially misleading docstring: readers auditing draft privacy will conclude non-member tenant colleagues are excluded when they are not.

**Fix:** Document all three grant paths (owner, explicit SpecDraftMember, same-tenant user) in both docstrings, or remove the tenant fallback if it is not the intended policy.

### M27. ValidationRuleFormData provides no numeric parsing, so rule routes duplicate raw float()/int() that 500s on bad input

`src/metaseed_hub/ui/spec_builder/forms.py:94` — consistency

`FieldFormData.get_constraints()` centralizes numeric parsing through `_parse_int`/`_parse_float`, giving field routes a catchable ValueError with a user-facing message (field_routes.py:203-206 renders it as a form error). `ValidationRuleFormData` carries the same string fields (minimum, maximum, min_items, max_items) but offers no equivalent accessor, so rule_routes.py:150-156 does `float(form_data.minimum)` / `int(form_data.min_items)` inline — a non-numeric entry in the rule form raises an unhandled ValueError and returns a 500 instead of the friendly error the sibling field form produces.

**Fix:** Add e.g. `get_minimum()/get_maximum()/get_min_items()/get_max_items()` (or a single `get_numeric_bounds()`) on ValidationRuleFormData using the existing `_parse_float`/`_parse_int` helpers, and have rule routes catch ValueError the same way field routes do.

### M28. Publishing a fork of another user's spec is always rejected with 403, contradicting the fork route

`src/metaseed_hub/ui/spec_builder/routes/draft_routes.py:182` — correctness

create_draft_from_spec (line 278) explicitly allows any signed-in user to fork any published spec ('Anyone may fork a published specification; that is the point') and records source_spec_id. But publish_draft (lines 182-188) does: `if existing_spec and not await can_edit_spec(session, user_id, existing_spec.id): raise HTTPException(403, "Cannot edit this spec")`. can_edit_spec returns True only for the source spec's author or an admin/owner in the source spec's tenant, so a forker who is neither can never publish their fork — the draft is permanently unpublishable even though publish creates a brand-new Spec row in the forker's own tenant and never modifies the source spec. The check guards an operation (editing the existing spec) that the code path does not perform.

**Fix:** Either drop the can_edit_spec gate when publishing creates a new Spec in the caller's own tenant, or clear source_spec_id when a non-editor forks so the fork publishes as an independent spec. Add a route-level test for fork-then-publish by a non-editor.

### M29. Unguarded float()/int() conversions turn malformed numeric form input into a 500

`src/metaseed_hub/ui/spec_builder/routes/rule_routes.py:150` — correctness

update_validation_rule converts numeric form fields directly: `rule.minimum = float(form_data.minimum) if form_data.minimum.strip() else None` (same for maximum, min_items, max_items at lines 150-156). A non-numeric value like 'abc' raises ValueError and produces a 500. The sibling field_routes.py update_field deliberately fixed this exact problem — it parses constraints inside try/except ValueError with the comment 'so malformed numeric input surfaces as a friendly form error instead of a 500' and forms.py provides _parse_int/_parse_float helpers with descriptive error messages — but rule_routes does not use either mechanism. Additionally, update_validation_rule allows the rule name to become empty (`rule.name = form_data.name.strip()` with no check), while add_validation_rule rejects empty names.

**Fix:** Move the numeric parsing into ValidationRuleFormData using the existing _parse_int/_parse_float helpers, catch ValueError in the route, and re-render validation_rules_list.html with the error like field_routes does. Reject empty names on update as add does.

### M30. delete_entity leaves dangling references in other entities and validation rules

`src/metaseed_hub/ui/spec_builder/routes/entity_routes.py:231` — correctness

update_entity's rename branch (lines 187-201) carefully rewrites every reference to the renamed entity: field.items, field.reference, field.parent_ref prefixes, and rule.applies_to (both string and list forms). delete_entity (lines 220-252) removes the entity and clears editing_entity/root_entity, but leaves all of those same references pointing at the now-nonexistent entity: fields in other entities keep items/reference/parent_ref values naming the deleted entity, and validation rules keep it in applies_to. The saved spec then contains references that cannot resolve, which will surface later as confusing validation/preview errors far from the delete action.

**Fix:** On delete, clear or update the same reference set the rename branch handles: null out field.items/reference/parent_ref that target the deleted entity, and remove it from rule.applies_to (dropping rules whose applies_to becomes empty, or flagging them).

## Low severity (17)

### L1. Websocket auth breaks out of db.session() async generator instead of using session_factory

`src/metaseed_hub/main.py:181` — consistency

The websocket room-authorization block runs `async for session in db.session(): await get_dataset_for_user(project_id, session, user); break`. Every other manual session user in the codebase (errors.py:86, ui/routes/auth.py:190, mcp/__init__.py:83) uses `async with db.session_factory() as session:`. Breaking out of an async generator does not run its cleanup at the `break`; the generator's `aclose()` (and thus the session close / connection return to the pool) is deferred to garbage collection, which is non-deterministic under an event loop. `db.session()` is designed as a FastAPI dependency where the framework closes the generator; driving it manually with `break` is the one divergent — and leak-prone — call site.

**Fix:** Replace the async-for/break with `async with db.session_factory() as session: await get_dataset_for_user(project_id, session, user)`.

### L2. verify_token silently ignores its settings argument after singleton init

`src/metaseed_hub/auth/__init__.py:256` — correctness

The standalone `async def verify_token(token: str, settings: Settings | None = None)` documents "Optional settings, uses default if not provided" and passes `settings` to `get_oidc_auth(settings)`. But `get_oidc_auth` only consumes `settings` when creating the module-level `_auth_instance` the first time; on every later call the argument is discarded and the previously constructed `OIDCAuth` (with its captured settings, cached OIDC discovery document, and cached JWKS) is returned. So calling `verify_token(token, settings=custom)` after any prior auth activity validates the token against the ORIGINAL issuer/client_id, not the ones in `custom`. The parameter's contract is not honored, and a caller supplying alternate settings (e.g. a second IdP, or a test) gets wrong verification with no error.

**Fix:** Either drop the `settings` parameter from the standalone `verify_token` (all current callers in main.py, ui/app.py, ui/dependencies.py, ui/routes/auth.py pass no settings), or make `get_oidc_auth` construct a fresh `OIDCAuth` when explicit settings differing from the singleton's are passed. At minimum document that `settings` is only honored on first initialization.

### L3. __all__ omits ApiToken and ErrorEvent

`src/metaseed_hub/models/__init__.py:830` — consistency

__all__ enumerates every model except ApiToken and ErrorEvent, yet both are part of the public surface: they are imported as `from metaseed_hub.models import ApiToken` / `ErrorEvent` in src/metaseed_hub/tokens.py, src/metaseed_hub/errors.py, src/metaseed_hub/ui/routes/auth.py, src/metaseed_hub/ui/routes/admin.py, and tests. The export list no longer honestly reflects the module's API.

**Fix:** Add "ApiToken" and "ErrorEvent" to __all__ (keeping alphabetical order).

### L4. save()/load() silently drop DatasetData.catalog_metadata

`src/metaseed_hub/repositories/dataset.py:133` — correctness

The `DatasetData` transfer object defined by `metaseed.repositories` carries a `catalog_metadata: CatalogMetadata | None` field (title, license, publisher, etc., used by the DCAT adapter). `save()` never persists it (`db_data` contains only profile/version/tree) and `load()` never populates it, so any catalog metadata a caller supplies is lost on round trip with no error. Grep confirms the hub currently never sets `catalog_metadata`, so there is no live data loss today, but the implementation silently violates the interface's transfer contract and will break the first caller that relies on it.

**Fix:** Persist `catalog_metadata` in the JSON `data` payload and restore it in `load()`, or raise/document explicitly that this backend does not support catalog metadata.

### L5. ACCESS_TOKEN_COOKIE constant defined in two modules

`src/metaseed_hub/ui/dependencies.py:19` — consistency

`ACCESS_TOKEN_COOKIE = "metaseed_access_token"` is defined independently here and again in src/metaseed_hub/ui/routes/auth.py:27. app.py imports the routes.auth copy for the token-refresh middleware while dependencies.py reads its own copy in get_current_user_from_cookie. If one literal is ever changed without the other, cookie writes and cookie reads silently diverge and every user appears logged out - a security-relevant constant should have a single source of truth.

**Fix:** Keep the constant in one module (dependencies.py is the lower-level of the two) and import it in routes/auth.py and app.py.

### L6. require_csrf decorator is never used by any route

`src/metaseed_hub/ui/security.py:90` — dead-code

Grepping the whole source tree, `require_csrf` is referenced only inside security.py itself and in tests/test_security.py. All production routes use `validate_csrf_or_error`/`csrf_error_response` directly (entity.py, dataset/*, auth.py) or the app-level `require_same_origin` dependency. The decorator (42 lines including its arg-scanning logic) is production-dead code kept alive only by its own unit tests, which vulture cannot flag because the tests count as usage.

**Fix:** Delete require_csrf and its tests, or migrate routes to use it; do not keep both an unused decorator and the manual call pattern.

### L7. parse_workbook_sheets: row[0] can raise IndexError on empty rows

`src/metaseed_hub/ui/helpers/uploads.py:66` — correctness

In the data-row loop, `first_val = str(row[0]) if row[0] else ""` indexes the tuple unconditionally. openpyxl in read_only mode can yield rows as empty tuples (rows with no cell records), in which case `row[0]` raises IndexError before the truthiness check runs, turning a workbook with a blank/formatted-but-empty row into a 500 on both import routes (crud.py:217 and editor.py:639). The header path is safe (enumerate) and the cell loop is guarded by `i < len(headers)`, but this one access is not.

**Fix:** Guard the access: `first_val = str(row[0]) if row and row[0] else ""` (or `if not row: continue` at the top of the loop).

### L8. ensure_dataset_facade duplicates spec-loading logic across two branches

`src/metaseed_hub/ui/helpers/dataset_state.py:67` — consistency

The `dataset.spec_draft_id` branch (lines 67-88) and the `dataset.spec_id` branch (lines 89-106) are near-identical: `session.get` the record, check `spec_data`, unwrap the nested `"spec"` key, `MetaseedClient.from_spec(raw_data)`, assign `state.facade` and `state.profile`, log warning/error otherwise. This is exactly the "duplicated logic that should be shared" pattern; the two copies have already started drifting (the draft branch logs a debug message on success and a comment about SpecBuilderState format that the published branch lacks), so a future fix to the unwrapping logic can easily land in only one branch.

**Fix:** Extract a helper, e.g. `_client_from_spec_data(spec_data: dict) -> MetaseedClient | None`, that does the unwrap + from_spec, and call it from both branches.

### L9. Hand-built HTML strings instead of Jinja partials; structural values interpolated unescaped

`src/metaseed_hub/ui/routes/table.py:56` — consistency

table.py assembles table-row and section HTML in Python f-strings (_build_primitive_row_html, _build_entity_row_html, and the inline block at lines 721-733), while sibling route modules (entity.py, admin dashboard) render Jinja templates/partials. Cell values are escaped, but structural interpolations are not: field_name (a user-controllable path parameter) goes unescaped into id="row-{field_name}-{row_idx}", hx-target selectors, hx-confirm text, and the <h4> at line 725; nested_type goes into hx-confirm and the placeholder paragraph. A field_name containing quotes breaks out of the attribute context. Practical exposure is limited (endpoints are POST/DELETE behind CSRF + tenant scoping), but it duplicates presentation logic and forgoes auto-escaping.

**Fix:** Move row/section markup into Jinja partials rendered via render_template (auto-escaping on), or at minimum html.escape() field_name and nested_type wherever they are interpolated.

### L10. Empty accession result is misreported as 'no importer' to the user

`src/metaseed_hub/ui/routes/dataset/crud.py:388` — correctness

create_dataset_from_accession raises `LookupError(f"Nothing was found for '{accession}'")` when the archive resolves nothing, reusing the same exception type as the no-importer case (line 361). The caller dataset_import_accession catches bare LookupError and redirects to `?error=no_importer` (line 533), so a valid importer returning an empty result tells the user no importer is registered for the profile — the wrong diagnosis, and precisely the failure the inline comment says should not be left for the user to discover. The HTMX route dataset_import_source distinguishes these two cases correctly (404 with distinct messages at lines 476 and 496).

**Fix:** Raise a distinct exception (e.g. ValueError or a small ImportEmptyError) for the nothing-found case and map it to a separate redirect error code, or check emptiness in the caller.

### L11. dataset_load_example returns a full Python traceback to the browser

`src/metaseed_hub/ui/routes/dataset/crud.py:798` — correctness

On failure, `tb = traceback.format_exc()` is embedded in the returned HTML (`<pre>...{e}\n\n{tb}</pre>`), exposing internal file paths, package layout, and code lines to the end user, and the traceback/exception text is not html-escaped either. No other route in this module returns tracebacks; they log via logger.exception and return a short message.

**Fix:** Log the traceback server-side (logger.exception already does) and return a short escaped error message, consistent with the other routes.

### L12. can_access_tenant is never called anywhere in the repo

`src/metaseed_hub/ui/spec_builder/access.py:109` — dead-code

Repo-wide grep (src, tests, templates) finds no caller of `async def can_access_tenant(...)`. Tenant access checks are done via `_user_can_access_draft` (which inlines the same User/tenant query) and `can_edit_spec`. vulture at min-confidence 80 does not flag unused module-level async functions reliably, so this slipped the gate. Project rules forbid dead code.

**Fix:** Delete `can_access_tenant`, or if tenant-scope checks are meant to be shared, have `_user_can_access_draft` call it instead of duplicating the query.

### L13. create_new_draft caches state without a revision, making the entry always stale

`src/metaseed_hub/ui/spec_builder/access.py:383` — correctness

`state_cache.set(draft.id, state)` is called without `revision=` even though `session.refresh(draft)` on line 381 has just made `draft.updated_at` available. The entry is stored with revision None, so the very next `load_state_for_draft` check `state_cache.revision(draft_id) == draft.updated_at` (line 284) always fails and the state is rebuilt from the row — the cache write is dead weight. Worse, if any future code path saved using this cached state without an intervening load, `expected_revision` would resolve to None and `save_state_to_draft` would raise a spurious DraftConflictError against a row nobody else touched.

**Fix:** Pass `revision=draft.updated_at` in `create_new_draft`'s `state_cache.set` call, mirroring lines 292 and 347.

### L14. Delete-draft branch in save_state_to_draft is unreachable and bypasses the conflict guard

`src/metaseed_hub/ui/spec_builder/access.py:321` — dead-code

`if state.spec is None: await session.delete(draft)...` can only trigger if a caller saves a state whose spec is None. Grep shows no caller ever does: `get_draft_context` raises 400 when `builder.spec is None`, `draft_routes.py:55-57` assigns `create_empty_spec()` before saving, and the only code that sets `spec = None` (`SpecBuilderState.reset`) is itself never called. Beyond being unreachable, the branch's semantics are hazardous: a 'save' that deletes the row, executed before the `expected_revision` conflict check, so it would destroy intervening edits without the DraftConflictError protection the rest of the function exists to provide.

**Fix:** Remove the branch (raise ValueError if state.spec is None), or if delete-via-empty-state is intended future behavior, move it after the revision check and document it.

### L15. Four SpecBuilderState methods are never called: reset, is_active, get_entity_names, get_current_entity_field_count

`src/metaseed_hub/ui/spec_builder/state.py:55` — dead-code

Repo-wide grep over src, tests, and Jinja templates finds no caller of `reset()` (the `state.reset()` hits in ui/routes/dataset/crud.py are on `AppState` from `get_dataset_state`, a different class), `is_active()` (hits in models/__init__.py and tokens.py are unrelated properties), `get_entity_names()`, or `get_current_entity_field_count()`. Routes use `mark_changed`/`mark_saved`/`reset_to_empty` and access `spec.entities` directly. Removing `reset()` also makes the unreachable delete branch in access.py:321 provably dead.

**Fix:** Delete the four methods and their tests if any; keep `reset_to_empty`, `mark_changed`, `mark_saved`, `to_dict`, `from_dict`, which are all used.

### L16. ReactionType(reaction) raises ValueError (500) on invalid form value

`src/metaseed_hub/ui/spec_builder/routes/comment_routes.py:175` — correctness

react_to_spec_comment declares `reaction: str = Form(...)` and then does `reaction_type = ReactionType(reaction)` with no guard. Any value outside the enum raises ValueError, which surfaces as a 500 instead of a 4xx. The same route already returns a clean 404 HTMLResponse for a missing comment, so the error-handling standard exists in the function. The identical pattern exists in member_routes.py line 142 (`member.role = SpecDraftRole(role)`), and field_routes.py add_field line 70 (`FieldType(field_type)`) / update_field via form_data.get_field_type() — all convert client-controlled strings to enums unguarded.

**Fix:** Declare the parameter with the enum type (`reaction: ReactionType = Form(...)`) so FastAPI returns 422 on invalid input, or wrap the conversion and return a 400 HTMLResponse. Apply the same fix to SpecDraftRole in member_routes and FieldType in field_routes.

### L17. SpecDraftRole(role) raises ValueError (500) on invalid role string

`src/metaseed_hub/ui/spec_builder/routes/member_routes.py:142` — correctness

update_spec_member_role does `member.role = SpecDraftRole(role)` where `role: str = Form(...)` is client-controlled. An unrecognized value raises ValueError and returns a 500 instead of a validation error. Same unguarded enum-conversion pattern as comment_routes.py line 175.

**Fix:** Type the form parameter as SpecDraftRole so FastAPI validates it (422), or catch ValueError and return a 400 response.

## Appendix: unverified low-confidence notes

These were reported by reviewers but not adversarially verified (low severity is not verified by default). Treat as leads, not confirmed defects.

- `src/metaseed_hub/backup.py:86` (typing) — _newest_per_bucket types its key function as object and suppresses mypy
- `src/metaseed_hub/backup.py:106` (dead-code) — The now parameter of select_expired and prune has no effect
- `src/metaseed_hub/backup.py:178` (correctness) — Stale .dump.partial files from a crashed run are never cleaned up
- `src/metaseed_hub/tokens.py:119` (naming) — active_tokens returns expired tokens despite claiming to return tokens that still work
- `src/metaseed_hub/main.py:141` (consistency) — robots.txt disallows /hub/matomo/ but the configured matomo path defaults to /matomo/
- `src/metaseed_hub/errors.py:78` (typing) — dispatch types call_next as Any and suppresses the resulting mypy error
- `src/metaseed_hub/database.py:80` (docstring) — Database.session docstring calls it an async context manager
- `src/metaseed_hub/api/datasets.py:91` (naming) — Underscore-prefixed `_user` parameter is actively used in every endpoint
- `src/metaseed_hub/auth/__init__.py:193` (consistency) — get_oidc_auth uses default-value Depends instead of the Annotated style
- `src/metaseed_hub/auth/__init__.py:29` (docstring) — keycloak_id docstring claims backwards-compat alias but it is the primary accessor
- `src/metaseed_hub/models/__init__.py:618` (consistency) — SpecCommentReaction duplicates _enum_values with an inline lambda
- `src/metaseed_hub/models/__init__.py:28` (dead-code) — Base.type_annotation_map is never exercised
- `src/metaseed_hub/models/mixins.py:37` (docstring) — SoftDeleteMixin docstring omits restore()
- `src/metaseed_hub/repositories/dataset.py:27` (consistency) — Asymmetric handling of entities without an entity_type between the two converters
- `src/metaseed_hub/repositories/dataset.py:185` (consistency) — Divergent transaction ownership between the two repository modules
- `src/metaseed_hub/repositories/dataset.py:49` (consistency) — Function-level uuid4 import with no justification
- `src/metaseed_hub/repositories/account.py:55` (design) — datasets_needing_new_owner and specs_needing_new_owner duplicate logic and issue N+1 queries
- `src/metaseed_hub/repositories/__init__.py:5` (consistency) — Package __all__ exports the unused repository but omits the used account API
- `src/metaseed_hub/mcp/__init__.py:202` (typing) — _editing and _building yield Any instead of their concrete types
- `src/metaseed_hub/mcp/__init__.py:270` (consistency) — _building writes JSONB without flag_modified, unlike the module's other JSONB writes
- `src/metaseed_hub/websocket/__init__.py:172` (consistency) — disconnect_redis mixes deprecated PubSub.close() with client aclose()
- `src/metaseed_hub/websocket/__init__.py:355` (design) — Client-supplied messages are rebroadcast without validating message type
- `src/metaseed_hub/ui/explore_routes.py:417` (consistency) — get_diff_graph lacks the empty-profile-tuples guard explore_compare has
- `src/metaseed_hub/ui/explore_routes.py:176` (typing) — _build_explore_catalog types the user parameter as Any
- `src/metaseed_hub/ui/explore_routes.py:305` (consistency) — Explore routes hand-roll auth instead of using the shared dependency aliases
- `src/metaseed_hub/ui/render.py:63` (dead-code) — get_version_info returns a 'branch' key that is never populated or read
- `src/metaseed_hub/ui/app.py:60` (design) — logging.basicConfig executed as an import side effect
- `src/metaseed_hub/ui/helpers/__init__.py:11` (dead-code) — Private CSRF helpers re-exported but unused outside csrf.py
- `src/metaseed_hub/ui/helpers/uploads.py:53` (correctness) — Read-only workbook is never closed
- `src/metaseed_hub/ui/helpers/dataset_state.py:154` (design) — save_dataset_state queries the User table even when no version will be created
- `src/metaseed_hub/ui/helpers/text.py:26` (docstring) — escape_pattern_hyphen docstring claims broader behavior than implemented
- `src/metaseed_hub/ui/helpers/text.py:0` (consistency) — text.py docstrings omit Google-style Args/Returns sections
- `src/metaseed_hub/ui/routes/table.py:553` (correctness) — Out-of-range and negative list indices are silently accepted
- `src/metaseed_hub/ui/routes/ontology_api.py:176` (dead-code) — Endpoints /cache/stats and /term/{term_id} have no callers anywhere
- `src/metaseed_hub/ui/routes/ontology_api.py:61` (dead-code) — Redundant clamp of rows already enforced by Query(ge=1, le=100)
- `src/metaseed_hub/ui/routes/ontology_api.py:19` (consistency) — Logger name diverges from sibling route modules
- `src/metaseed_hub/ui/routes/auth.py:317` (consistency) — Two different CSRF validation idioms within the same file
- `src/metaseed_hub/ui/routes/entity.py:82` (consistency) — Bare `except Exception` around validate_csrf_or_error masks non-CSRF failures
- `src/metaseed_hub/ui/routes/table.py:238` (dead-code) — Unused `request` parameter in add_table_row
- `src/metaseed_hub/ui/routes/table.py:638` (dead-code) — Debug-leftover INFO logs dumping entity payloads in update_single_entity_field
- `src/metaseed_hub/ui/routes/table.py:243` (typing) — Mutation handlers type the user as OptionalUser although auth is guaranteed
- `src/metaseed_hub/ui/routes/dataset/comments.py:218` (correctness) — Invalid reaction value raises unhandled ValueError (500)
- `src/metaseed_hub/ui/routes/dataset/members.py:137` (correctness) — Invalid role value raises unhandled ValueError (500)
- `src/metaseed_hub/ui/routes/dataset/crud.py:706` (correctness) — Hand-built HX-Trigger JSON breaks on non-quote control characters and leaks internals
- `src/metaseed_hub/ui/routes/dataset/crud.py:782` (dead-code) — Leftover debug logging loop dumps the entity tree on every example load
- `src/metaseed_hub/ui/routes/dataset/versions.py:25` (dead-code) — Unused module-level logger in versions.py and members.py
- `src/metaseed_hub/ui/routes/dataset/comments.py:241` (dead-code) — Stale section-header comments left over from the monolith split
- `src/metaseed_hub/ui/routes/dataset/editor.py:39` (typing) — _build_dataset_context types the session parameter as Any
- `src/metaseed_hub/ui/services/entity_service.py:320` (consistency) — Raw exception text leaks into user-facing error messages, unlike delete_entity
- `src/metaseed_hub/ui/services/entity_service.py:82` (dead-code) — EntityService.client property is never used
- `src/metaseed_hub/ui/services/entity_service.py:309` (dead-code) — Update branch assigns `entity` but never uses it
- `src/metaseed_hub/ui/services/entity_service.py:65` (docstring) — EntityService docstring omits the `user` constructor argument
- `src/metaseed_hub/ui/services/entity_service.py:9` (docstring) — "Uses MetaseedClient public API exclusively" claim is inaccurate
- `src/metaseed_hub/ui/services/entity_service.py:49` (typing) — EntitySaveResult.node typed Any with an incorrect circular-import justification
- `src/metaseed_hub/ui/services/entity_service.py:246` (consistency) — get_helper puts developer-facing text in user_message
- `src/metaseed_hub/ui/spec_builder/cache.py:64` (dead-code) — StateCache.__contains__ is never used
- `src/metaseed_hub/ui/spec_builder/access.py:300` (typing) — expected_revision default is a sentinel not covered by its declared type
- `src/metaseed_hub/ui/spec_builder/access.py:356` (docstring) — create_new_draft has a one-line docstring while every sibling documents Args/Returns
- `src/metaseed_hub/ui/spec_builder/forms.py:104` (docstring) — Comment claims 'field' is a keyword; it is not
- `src/metaseed_hub/ui/spec_builder/routes/list_routes.py:143` (correctness) — create_new_spec silently swallows a failed template clone
- `src/metaseed_hub/ui/spec_builder/routes/draft_routes.py:426` (dead-code) — export_yaml re-imports load_state_for_draft that is already imported at module level
- `src/metaseed_hub/ui/spec_builder/routes/draft_routes.py:51` (consistency) — Unused unpacked context variables ignore the module's underscore convention
- `src/metaseed_hub/ui/spec_builder/routes/comment_routes.py:107` (correctness) — Empty comments are accepted
