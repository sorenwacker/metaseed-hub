# Codebase Review

Date: 2026-06-18. Reviewed source root `src/metaseed_hub` (38 source files, ~12,090 LOC) with a per-module multi-agent pass; every high/medium finding was adversarially verified before inclusion. metaseed dependency was at v0.9.1 during the review.

## Remediation status

All 31 confirmed findings were remediated on 2026-06-18, each with a failing test first where testable, atomic commits, and the full gate suite green throughout. Summary by theme:

- Multi-tenant isolation / authorization (#3, #6, #8, #18, #24): dataset REST API scoped to the caller's tenant; spec-builder membership requires draft ownership; comments require draft access; explore profile loading scoped to tenant.
- Soft-delete consistency (#1, #2, #15): API uses soft-delete and filters `deleted_at`; `save()` restores a soft-deleted name instead of colliding.
- HTML injection (#5): inline table cell values are escaped.
- WebSocket fan-out (#7, #27): delivery routed through the Redis listener; no duplicate local send.
- Reaction enum (#4): persists lowercase values matching the migration.
- Data loss (#14, #19, #20, #25): `save()` no longer mutates caller entities; correct label derivation; import honors the selected profile/version; spec-builder state round-trips losslessly via Pydantic.
- Auth hot path (#9, #10, #11): standalone `verify_token` reuses the singleton; JWKS refreshes on key rotation; the always-None `TokenUser.tenant_id` removed.
- Duplication / dead code (#16, #22, #23, #28, #29, #30, #31): single `render_template`/`get_version_info`; dead `spec_adapters` module and unused functions removed.
- Typing / consistency / size (#12, #13, #21, plus the 1000-LOC rule): nullable `created_by` typing; dataset listing ordered by modified; `entityChanged` emitted on primitive-list edits; `dataset.py` split into a package.

The findings below are retained as the original review record. The unverified appendix items remain candidates; those below the project's vulture threshold were intentionally left in place.

## Baseline gates

These are the project's own canonical commands (Makefile / pre-commit), run before the review.

| Gate | Command | Result |
|------|---------|--------|
| Lint | `uv run ruff check src tests` | Pass |
| Types | `uv run mypy src` (strict) | Pass, 48 files |
| Dead code | `uv run vulture src vulture_whitelist.py --min-confidence=80` | Pass |
| Tests | `uv run pytest` | 179 passed, 1 skipped (requires Postgres on :7432 via `make up`) |
| File size | 1000 LOC project rule | Fail: `ui/routes/dataset.py` is 1858 LOC |

Open marker: `TODO` at `ui/routes/dataset.py:164` (load user's custom specs).

## Summary

- Total findings: 72 (confirmed 31, refuted 5, unverified low-severity 36).
- Confirmed by severity: high 7, medium 20, low 4.
- Confirmed by category: consistency 6, correctness 19, dead-code 4, design 1, typing 1.

### Recurring themes

1. **Multi-tenant isolation / authorization gaps.** The REST `api/datasets.py` endpoints and several spec-builder routes (`member_routes`, `comment_routes`) and `explore_routes` act on rows by id without scoping to the caller's tenant or checking draft membership, bypassing the tenant-scoped repository layer. (Findings #3, #6, #8, #18, #24.)
2. **Soft-delete not honored consistently.** The API hard-deletes and omits the `deleted_at IS NULL` filter the repository applies everywhere, and `save()` can collide with a soft-deleted unique name. (Findings #1, #2, #15.)
3. **Duplicated logic that has already drifted.** Two `get_version_info`, two `render_template`, two client-loading paths, three label-derivation copies, and a dead `spec_adapters.py` module. (Findings #16, #22, #23, #28, #29.)
4. **Hand-rolled (de)serialization of Pydantic v2 models loses data** on the draft clone/save round-trip. (Findings #25, #14.)
5. **Redis pub/sub WebSocket fan-out is non-functional** (no listener task) and, once fixed, would double-deliver. (Findings #7, #27.)

## Confirmed findings — High

### 1. `api/datasets.py:188` — delete_dataset hard-deletes a soft-delete model, contradicting the established pattern
*Category: correctness*

Dataset inherits SoftDeleteMixin (models/mixins.py) and the canonical DatabaseDatasetRepository.delete() performs `dataset.soft_delete()` (repositories/dataset.py:242). The API endpoint instead does `await session.delete(dataset)` followed by `await session.commit()`, permanently removing the row. This is inconsistent with the rest of the codebase and destroys data that the soft-delete design intends to retain (and breaks restore()).

**Fix:** Replace `await session.delete(dataset)` with `dataset.soft_delete()` then `await session.commit()`, matching the repository pattern.

### 2. `api/datasets.py:63` — API queries do not filter soft-deleted rows (deleted_at IS NULL)
*Category: correctness*

Every read query in this module selects datasets without `Dataset.deleted_at.is_(None)`. The repository layer applies this filter on every query (repositories/dataset.py lines 111, 148, 201, 234, 261). As written, list_datasets (line 63), get_dataset (line 115), update_dataset (line 146) and delete_dataset (line 180) will return, mutate, or operate on datasets that were soft-deleted via the repository/UI, exposing deleted data through the API.

**Fix:** Add `Dataset.deleted_at.is_(None)` to the where clause of each select, consistent with DatabaseDatasetRepository.

### 3. `api/datasets.py:96` — get/update/delete by id are not tenant-scoped (cross-tenant IDOR)
*Category: correctness*

get_dataset (line 115), update_dataset (line 146) and delete_dataset (line 180) select on `Dataset.id == dataset_id` only, with no tenant filter. `_user: TokenUser` (which carries `tenant_id`, see auth/__init__.py:24) is injected but never used for authorization. Any authenticated user, regardless of their tenant, can read, modify, or delete any dataset whose UUID they know. This is a multi-tenant isolation violation; the repository is explicitly tenant-scoped (repositories/dataset.py:88-100) but these endpoints bypass it.

**Fix:** Resolve the caller's tenant from the token (or require/validate a tenant_id) and add `Dataset.tenant_id == <user tenant>` to the where clause of get/update/delete, rejecting datasets outside the caller's tenant with 404.

### 4. `models/__init__.py:423` — reactiontype enum configured inconsistently between CommentReaction and SpecCommentReaction, with value-casing mismatch vs migration
*Category: consistency*

CommentReaction.reaction (line 423) uses `Enum(ReactionType)` while SpecCommentReaction.reaction (line 501) uses `Enum(ReactionType, name="reactiontype", create_type=False)`. Both target the same logical PostgreSQL enum but are configured differently. More importantly, by SQLAlchemy default `Enum(ReactionType)` persists the StrEnum member NAME (`LIKE`/`DISLIKE`, uppercase). The migration 03d97af76817 creates the PG type `reactiontype` with the lowercase VALUES `('like','dislike')`. Tests pass only because conftest.py uses `Base.metadata.create_all` (the type is generated from the model, so it accepts LIKE/DISLIKE), but against the migration-built production schema an insert of `ReactionType.LIKE` would send `'LIKE'` and violate the PG enum, raising InvalidTextRepresentation. The two reaction columns must be configured identically and aligned with the migration's lowercase values.

**Fix:** Set `values_callable=lambda e: [m.value for m in e]` on both reaction columns so the lowercase `.value`s are persisted (matching the migration), and configure both columns identically (same name= / create_type= handling).

### 5. `ui/routes/table.py:189` — Cell/list values interpolated into HTML without escaping (HTML injection)
*Category: correctness*

User-controlled field values from model_dump are spliced directly into raw HTML f-strings without escaping. In _build_entity_row_html: `cell_value = instance_data.get(col, "")` then `display_value = cell_value or "Click to edit"` is inserted as `<span class="cell-display...">{display_value}</span>` and `value="{cell_value}"`. A value containing a double-quote or `<script>` breaks out of the attribute/element. The sibling route src/metaseed_hub/ui/routes/dataset.py (lines 1026-1027) explicitly uses `html_module.escape(...)` for user content, so this file is both incorrect and inconsistent with the established pattern.

**Fix:** Escape all interpolated values with html.escape() before building the HTML (cell_value, display_value, and the primitive input value), matching dataset.py.

### 6. `ui/spec_builder/routes/member_routes.py:69` — Member routes never verify the caller may access the target draft
*Category: correctness*

`add_spec_member`, `update_spec_member_role`, and `remove_spec_member` operate on the path `draft_id` using only `UserContextDep`, which verifies authentication but not authorization. None of them check that the authenticated user owns or is a member of `draft_id` (unlike sibling draft routes which use `DraftContextDep` / explicit `draft.user_id == user_id` checks, e.g. draft_routes.py:105 and the `leave_spec` handler in this same file at line 178). Any logged-in user can add/remove members or change roles on an arbitrary draft by guessing/knowing its id. SpecDraftMember rows are also created against `draft_id` without confirming the draft exists.

**Fix:** Load the draft and call `_user_can_access_draft` (or require owner role) before mutating membership, mirroring the ownership guard already used in `leave_spec` and the draft routes.

### 7. `websocket/__init__.py:60` — Redis pub/sub listener task is never started; cross-instance broadcasting is non-functional
*Category: correctness*

The class docstring claims it 'Manages WebSocket connections with Redis pub/sub for scaling.' connect_redis() creates self._pubsub and join_room() subscribes to per-project channels, and broadcast_to_room() publishes messages to Redis. However nothing ever consumes messages from the pubsub: there is no asyncio.create_task / listen loop anywhere. self._listener_task is declared as None at line 60, never assigned, and only ever read in disconnect_redis() (lines 70-73) where the `if self._listener_task:` guard is always False. As a result, messages published to Redis by one instance are never read or re-delivered to local WebSocket clients on other instances. Multi-instance fan-out (the stated purpose of the Redis integration) silently does nothing; only same-instance local sends in broadcast_to_room work. Grep across src/ and tests/ confirms no create_task/ensure_future and no listener implementation exist.

**Fix:** Implement a listener coroutine that iterates self._pubsub.listen()/get_message(), parses each payload, and forwards it to local room connections (without re-publishing). Start it via self._listener_task = asyncio.create_task(...) in connect_redis(). To avoid duplicate delivery, the local send in broadcast_to_room should be removed and all delivery routed through the listener (the publishing instance also receives the message back), or the published payload should carry an origin-instance id that the listener filters.

## Confirmed findings — Medium

### 8. `api/datasets.py:49` — list_datasets accepts an arbitrary tenant_id without checking it against the caller
*Category: correctness*

list_datasets takes `tenant_id: str` as a query parameter and filters on it, but never checks it against the authenticated user's tenant (`_user` is unused). Any authenticated user can list datasets of any tenant by passing a different tenant_id. Combined with the multi-tenant rule that tenant scoping must be enforced, this leaks data across tenants.

**Fix:** Derive the tenant from the authenticated TokenUser (or validate that the requested tenant_id matches the user's tenant / membership) before listing.

### 9. `auth/__init__.py:237` — Standalone verify_token refetches OIDC discovery + JWKS on every call
*Category: correctness*

The module-level `verify_token` constructs a fresh `OIDCAuth(settings)` instance per call: `auth = OIDCAuth(settings); return await auth.verify_token(token)`. Because `_oidc_config` and `_jwks` caches live on the instance, every invocation performs two outbound HTTP requests (discovery document + JWKS). This standalone function is the one actually used on the cookie-auth hot path (`ui/dependencies.py:get_current_user_from_cookie` calls it on every request, and `ui/app.py` middleware calls it too), so every authenticated request triggers two network round-trips to the OIDC provider. The dependency-injected `get_oidc_auth` reuses a singleton and caches correctly; the standalone path defeats that.

**Fix:** Reuse the cached singleton (e.g. call get_oidc_auth(settings) / share the module-level _auth_instance) instead of building a new OIDCAuth each call, or cache JWKS/discovery at module/class level with a TTL.

### 10. `auth/__init__.py:78` — JWKS cache never refreshes on key rotation / unknown kid
*Category: correctness*

`get_jwks` caches `_jwks` permanently (`if self._jwks is not None: return self._jwks`). In `verify_token`, when no key matches the token's `kid`, it raises 401 ("Unable to find appropriate key") without ever re-fetching the JWKS. After the provider rotates signing keys, the singleton instance returned by `get_oidc_auth` keeps a stale key set and rejects all valid tokens signed with the new key until the process restarts.

**Fix:** On a kid miss, invalidate the cache (set self._jwks = None) and re-fetch once before failing, or attach a TTL to the cached JWKS.

### 11. `auth/__init__.py:24` — TokenUser.tenant_id is declared but never populated
*Category: correctness*

`TokenUser` declares `tenant_id: str | None = None`, but `verify_token` never sets it (the TokenUser(...) construction at lines 155-160 omits tenant_id) and grep across src/ and tests/ finds no `tenant_id=` assignment in the auth module nor any read of `TokenUser.tenant_id`. In a multi-tenant app this field reads as if tenant scoping flows from the token, but it is always None. Tenant resolution actually happens elsewhere via `keycloak_id[:8]` slug logic. The unused, always-None field is misleading dead state.

**Fix:** Either populate tenant_id from a token claim during verify_token, or remove the field so it does not imply token-derived tenant scoping that does not exist.

### 12. `models/__init__.py:548` — Spec.created_by_id annotated non-nullable but column and FK are nullable (SET NULL)
*Category: typing*

created_by_id is declared `Mapped[str]` (non-optional) while the column has `nullable=True` and `ForeignKey("users.id", ondelete="SET NULL")`. The migration d5b1a0f1f999 also creates the column as `nullable=True` with `ondelete="SET NULL"`. The attribute can therefore legitimately be None at runtime (after the owner user is deleted), but the type hint promises `str`. Likewise `created_by: Mapped["User"]` at line 556 should be `Mapped["User | None"]`. This is a mypy --strict type-safety bug. The sibling model DatasetVersion (lines 239 and 247) does this correctly with `Mapped[str | None]` / `Mapped["User | None"]`.

**Fix:** Change line 548 to `created_by_id: Mapped[str | None]` and line 556 to `created_by: Mapped["User | None"] = relationship("User")`, matching DatasetVersion.

### 13. `repositories/dataset.py:102` — list() does not sort by modified time, violating the parent ABC contract
*Category: correctness*

The parent AsyncDatasetRepository.list() docstring (metaseed/repositories/dataset_repository.py) specifies: 'List of DatasetInfo summaries, sorted by modified time (most recent first).' This implementation issues `select(Dataset).where(...)` with no `order_by` clause and returns rows in arbitrary database order, so callers relying on the documented ordering get unsorted results. The hub docstring also omits the ordering guarantee, diverging from the interface it implements.

**Fix:** Add `.order_by(Dataset.updated_at.desc())` to the select, or sort the resulting DatasetInfo list by `modified` descending before returning.

### 14. `repositories/dataset.py:153` — save() mutates the caller's entity dicts via shallow copy + dict.pop
*Category: correctness*

save() does `tree = _entities_to_tree(data.entities.copy())`. `list.copy()` is a shallow copy: it creates a new list but the inner dict objects are shared with the caller's `DatasetData.entities`. Inside `_entities_to_tree` each entity is mutated in place: `entity.pop("_type", None)` and `entity.pop("_parent_unique_id", None)` (dataset.py:55-56). As a result, after a save() call the caller's original DatasetData.entities have lost their `_type` and `_parent_unique_id` keys. This is a silent side effect on an input argument and will corrupt any caller that reuses the DatasetData (e.g. saving then re-saving, or logging).

**Fix:** Deep-copy each entity before mutation, e.g. pass `[dict(e) for e in data.entities]` (and ideally make `_entities_to_tree` not mutate its inputs at all by reading keys instead of popping, building the data dict explicitly).

### 15. `repositories/dataset.py:144` — save() insert path can violate uq_datasets_tenant_name after a soft delete
*Category: correctness*

save() looks up the existing dataset with `Dataset.deleted_at.is_(None)` (dataset.py:144-151). If a dataset with the same (tenant_id, name) was previously soft-deleted, that filter excludes it, so `scalar_one_or_none()` returns None and the code takes the INSERT branch (dataset.py:165-172). However the model defines `UniqueConstraint("tenant_id", "name", name="uq_datasets_tenant_name")` (models/__init__.py:178) which is NOT scoped to deleted_at, so the soft-deleted row still occupies the name and the INSERT raises an IntegrityError at commit. The same is true of exists()/load() correctly hiding soft-deleted rows, making the name appear free while the DB rejects reuse. Net effect: a name cannot be reused after deletion, contradicting the soft-delete intent.

**Fix:** Either include soft-deleted rows in the save() lookup and restore/overwrite them, or make the uniqueness a partial index scoped to `deleted_at IS NULL`. Add a regression test for save-after-delete with the same name.

### 16. `ui/app.py:262` — app.py re-defines render_template instead of reusing render.render_template
*Category: consistency*

The nested render_template() in create_hub_app (lines 262-292) duplicates the canonical render.py render_template() (lines 102-149) that every route module imports and uses (entity.py, dataset.py, admin.py, auth.py). The logic (CSRF token injection, version_info, cookie set) is the same but maintained twice, and it pulls in the divergent app-local get_version_info. The home/login routes use this private copy while the rest of the app uses render.render_template.

**Fix:** Call render.init_templates(templates) (already done for route modules) and reuse render.render_template in the home and login routes rather than defining a local copy.

### 17. `ui/dependencies.py:130` — New tenant can be lost when user already exists in ensure_tenant_and_user
*Category: correctness*

ensure_tenant_and_user() creates a new Tenant with only `await session.flush()` (line 154) and relies on the subsequent `await session.commit()` inside the db_user-creation branch (line 167) to persist everything. But if the tenant is missing while db_user already exists, the commit branch is skipped and the function returns without committing. get_session() (database.py session()) does NOT auto-commit on context exit, so the newly created tenant is rolled back when the session closes. Code:

    if not tenant:
        tenant = Tenant(...)
        session.add(tenant)
        await session.flush()
    ...
    if not db_user:
        db_user = User(...)
        session.add(db_user)
        await session.commit()
    return tenant, db_user

**Fix:** Commit unconditionally before returning (e.g. always `await session.commit()` at the end), or commit immediately after creating the tenant.

### 18. `ui/explore_routes.py:89` — load_profile_spec loads Spec/SpecDraft by id with no tenant or soft-delete filtering
*Category: correctness*

In load_profile_spec, the 'draft:' branch (line 91: `select(SpecDraft).where(SpecDraft.id == draft_id)`) and the 'spec:' branch (line 101: `select(Spec).where(Spec.id == spec_id)`) filter only by primary id. There is no tenant_id scoping and, for the published Spec branch, no `Spec.deleted_at.is_(None)` filter. These functions are reached via user-supplied form/path parameters in /compare (line 316 form.getlist) and /graph (line 370 profiles split), so an authenticated user can load specs/drafts belonging to other tenants, or soft-deleted published specs, by supplying their id. IDs are UUIDs (hard to enumerate), which mitigates but does not remove the cross-tenant access path. This contradicts the project's multi-tenant scoping rule.

**Fix:** Pass the caller's tenant into load_profile_spec / load_profiles_for_comparison and add `.where(Spec.tenant_id == tenant.id)` / `.where(SpecDraft.tenant_id == tenant.id)`, plus `Spec.deleted_at.is_(None)` on the published-spec branch.

### 19. `ui/helpers.py:326` — Operator precedence bug overwrites already-set entity label
*Category: correctness*

In create_nested_nodes the Person special-case label is guarded incorrectly:

    if not label_set and item_data.get("first_name") or item_data.get("last_name"):
        parts = [item_data.get("first_name", ""), item_data.get("last_name", "")]
        child_node.label = " ".join(p for p in parts if p)

Python parses this as `(not label_set and item_data.get("first_name")) or item_data.get("last_name")`. So whenever an item has a truthy `last_name`, the branch runs even if `label_set` is already True (a label was found via LABEL_FIELDS such as title/name/id). This overwrites the correct identifier-based label with a first/last name composite. The intended guard is that the whole first_name/last_name disjunction should only be considered when `label_set` is False.

**Fix:** Wrap the name check in parentheses and gate it entirely on not label_set, e.g.:

    if not label_set and (item_data.get("first_name") or item_data.get("last_name")):
        ...

Note the correct form is already used in forms.get_label_from_values (lines 103-105) as a separate `if`.

### 20. `ui/routes/dataset.py:201` — dataset_import discards user-supplied profile/version form fields
*Category: correctness*

dataset_import accepts `profile` and `version` as form parameters (lines 177-178), but lines 201-202 immediately overwrite them:

    data = None
    profile = None
    version = None

This unconditionally throws away whatever the user submitted in the form. The subsequent detection logic (lines 264-267) only repopulates them if the parsed file content happens to contain `profile`/`version`/`_profile`/`_version` keys; otherwise it falls back to the hardcoded `miappe` default (lines 270-275). The explicit form selection is never honored, so a user importing an ISA/DwC/etc. file and selecting the matching profile still gets miappe.

**Fix:** Remove the `profile = None` and `version = None` reassignments on lines 201-202 (keep only `data = None`), so the form-supplied values survive and only the `if not profile`/`if not version` detection fallbacks apply.

### 21. `ui/routes/table.py:493` — update_primitive_list_item omits HX-Trigger 'entityChanged' set by all sibling mutations
*Category: consistency*

Every other mutating handler in this file returns an HX-Trigger 'entityChanged' header (add_table_row L299/L380, update_table_cell L440, delete_primitive_list_item L542, update_single_entity_field L648, delete_single_entity_field L717). update_primitive_list_item returns `HTMLResponse(status_code=200)` with no HX-Trigger, so editing a primitive list item silently fails to notify the client to refresh, unlike adding/deleting one. This is an inconsistency that likely produces a stale UI after edits.

**Fix:** Add `headers={"HX-Trigger": "entityChanged"}` (or set response.headers) to match the other mutation handlers.

### 22. `ui/services/entity_service.py:93` — Client-loading logic duplicated between EntityService and helpers.py
*Category: consistency*

ensure_state / _load_client_for_draft_spec / _load_builtin_client (lines 93-199) reimplement the same client construction that already exists in metaseed_hub/ui/helpers.py (around lines 782-819): both branch on dataset.spec_draft_id, both fetch SpecDraft, both unwrap the nested 'spec' key (`if isinstance(raw_data, dict) and "spec" in raw_data: raw_data = raw_data["spec"]`), both call MetaseedClient.from_spec / MetaseedClient(profile, version), both set state.facade = client.facade, and both call client.load(dataset.data). This is duplicated logic that should be shared; the two copies have already drifted (see the state.profile normalization finding), which is exactly the failure mode duplication causes.

**Fix:** Extract one shared helper (e.g. a function that builds a configured client+AppState from a Dataset and AsyncSession) and have both EntityService and helpers.py call it.

### 23. `ui/spec_adapters.py:0` — Entire spec_adapters.py module is unused dead code
*Category: dead-code*

All four classes (SpecPersistence, SpecProvider, DatabaseSpecPersistence, DatabaseSpecProvider) and the BUILTIN_PROFILES constant are defined here but never imported or referenced anywhere in src/, tests/, or docs/. Verified via grep across the whole tree: the only external mention is a stale docstring in explore_routes.py line 4 ('and DatabaseSpecProvider for unified spec access'), but explore_routes.py actually uses its own HubSpecLoader and never instantiates DatabaseSpecProvider. The module docstring even acknowledges it is a placeholder pending a metaseed export. This is ~436 lines of unreachable code carrying its own copies of tenant-scoping/version-ordering logic that can silently rot.

**Fix:** Remove the module entirely, or wire DatabaseSpecProvider into explore_routes.py to replace the duplicated inline spec-loading logic there. Also fix the stale reference in explore_routes.py docstring.

### 24. `ui/spec_builder/routes/comment_routes.py:73` — Comment routes never verify caller may access the target draft
*Category: correctness*

`get_spec_comments`, `add_spec_comment`, and `react_to_spec_comment` use only `UserContextDep` and act on the path `draft_id` with no check that the user owns or is a member of that draft. Any authenticated user can read all comments on, and post comments/reactions to, any draft by id. `delete_spec_comment` does guard with `comment.user_id == user_id`, so the gap is specifically in read/create/react. This diverges from the draft routes, which gate access via `DraftContextDep`/`_user_can_access_draft`.

**Fix:** Verify draft access (e.g. via `_user_can_access_draft`) before listing, creating, or reacting to comments, consistent with the draft route guards.

### 25. `ui/spec_builder/state.py:68` — Lossy manual serialization drops several schema fields on round-trip
*Category: correctness*

The hand-written spec_to_dict / dict_to_spec round-trip silently drops fields that exist on the Pydantic v2 schema models. _rule_to_dict / _dict_to_rule omit ValidationRuleSpec.type, .message, .lat_field, .lon_field, .start_field, .end_field. spec_to_dict / dict_to_spec omit ProfileSpec.spec_version and ProfileSpec.ontologies, and the entity serialization omits EntityDefSpec.example. Because published specs are cloned into drafts via SpecBuilderState.from_dict(spec.spec_data) (draft_routes.py:296,302 -> create_new_draft) and saved back via to_dict, any spec loaded from a template with coordinate_pair/date_range rules, a spec_version, an ontologies dict, or entity examples loses that data on the first save. This is a data-loss bug for cloned templates.

**Fix:** Since these are Pydantic v2 models, replace the manual _field_to_dict/_rule_to_dict/spec_to_dict/dict_to_spec helpers with spec.model_dump(mode="json", exclude_none=True) and ProfileSpec.model_validate(data). This is lossless and removes ~170 lines of error-prone hand-mapping.

### 26. `ui/spec_builder_helpers.py:153` — Inconsistent 'latest version' index convention between helpers and adapters
*Category: consistency*

list_available_templates (line 153) treats `versions[-1]` as the latest version ('Get display info from latest version'), while DatabaseSpecProvider.get_display_name in spec_adapters.py line 416 and the explore_index route (explore_routes.py line 207) treat `versions[0]` as the version to load for the display name. SpecLoader.list_versions is documented in spec_adapters.py:336 as 'newest first', which implies index 0 is newest, contradicting the `versions[-1]` usage in the helper. One of the two conventions selects the wrong (oldest vs newest) version when deriving display metadata. Since spec_adapters.py is dead, the live discrepancy is between list_available_templates ([-1]) and explore_index ([0]); they will pick different versions' display_name for the same profile.

**Fix:** Pin down SpecLoader.list_versions ordering and use a single convention everywhere (e.g. always versions[0] if newest-first). Add a test asserting the ordering so the index choice is verified.

### 27. `websocket/__init__.py:188` — broadcast_to_room both publishes to Redis and sends locally, causing duplicate delivery once a listener exists
*Category: design*

broadcast_to_room() publishes message_json to the Redis channel (lines 188-190) and then also sends to all local connections (lines 193-200). With the intended pub/sub listener in place, the publishing instance would receive its own message back through the subscription and deliver it to local clients a second time, duplicating every message for users on the originating instance. Currently this is masked only because no listener exists (see the high-severity finding), so it is latent rather than active.

**Fix:** Pick one delivery path: either deliver locally and publish for OTHER instances only (e.g. tag payloads with an origin id and have the listener skip its own), or always route delivery through the Redis listener and drop the direct local send.

## Confirmed findings — Low

### 28. `ui/app.py:78` — Two divergent get_version_info implementations produce different dicts
*Category: consistency*

app.py defines get_version_info() (lines 78-132) that shells out to git and returns keys {version, commit, branch, short_commit}. render.py defines a separate get_version_info() (lines 56-71) that reads only the package _version and returns {version, short_commit, branch} (no `commit`). render.py's render_template injects the render.py version, while explore_routes.py and spec_builder/routes/_common.py import the app.py version, and app.py's own render_template uses the app.py version. Pages therefore receive different version_info shapes depending on which route rendered them, and a template referencing version_info.commit breaks on render.py-rendered pages.

**Fix:** Keep a single get_version_info() (the package-based one in render.py) and have app.py, explore_routes, and spec_builder import it instead of maintaining a second implementation.

### 29. `ui/forms.py:88` — get_label_from_values is never used in production code
*Category: dead-code*

get_label_from_values is defined in forms.py and imported only by tests/test_forms.py; grepping the whole source tree (src/ and templates) shows no production caller. Label derivation in production is done via helper.get_label (helpers.add_entity_node line 82) and the inline LABEL_FIELDS loop in create_nested_nodes (helpers.py lines 320-328). The function duplicates that logic and is unreferenced outside its own test.

**Fix:** Remove get_label_from_values (and its test) or replace the duplicated inline label logic in create_nested_nodes with a single shared call to it, so there is one honest implementation of label derivation instead of three copies.

### 30. `ui/routes/dataset.py:597` — _get_entity_info is never called
*Category: dead-code*

`_get_entity_info(state: AppState)` (lines 597-610) is defined but never referenced anywhere in the source tree or tests (grep for `_get_entity_info` returns only its definition). Its logic (iterating `facade.entities` and collecting `name`/`description`) is duplicated inline inside `_build_dataset_context` (lines 633-637), which is the function actually used. This is dead code.

**Fix:** Delete `_get_entity_info`; the equivalent logic already lives in `_build_dataset_context`.

### 31. `websocket/__init__.py:232` — Public method get_room_presence is never called
*Category: dead-code*

WebSocketManager.get_room_presence (lines 232-243) is a public method but is never referenced anywhere in src/ or tests/. Grep for 'get_room_presence' returns only its definition. The internal Room.get_presence() is used directly inside join_room/leave_room, so this manager-level wrapper is unused.

**Fix:** Remove get_room_presence, or wire it into an endpoint/handler if exposing room presence over HTTP/WS is intended.

## Appendix — unverified low-severity notes

These were reported by the review pass but not run through adversarial verification (low severity). Treat as candidates, confirm before acting.

- `ui/dependencies.py:31` [consistency] — get_or_create_csrf_token and CSRF_TOKEN_COOKIE duplicated from helpers
- `ui/routes/table.py:251` [consistency] — Logger created inside handler via local 'import logging' instead of module-level logger
- `ui/routes/table.py:391` [consistency] — update_table_cell return type annotated as Response while siblings use HTMLResponse
- `ui/spec_builder/access.py:36` [consistency] — Redundant pass in exception class body with docstring
- `ui/spec_builder/routes/draft_routes.py:155` [consistency] — reset_draft bypasses the shared save path used by every sibling route
- `ui/spec_builder/routes/draft_routes.py:325` [consistency] — update_profile_metadata inlines form handling while sibling routes delegate to a form dataclass
- `ui/spec_builder/routes/field_routes.py:187` [consistency] — ontologies parsing is inlined in the route instead of living on FieldFormData
- `ui/spec_builder_helpers.py:42` [consistency] — TYPE_CHECKING import block placed after a public function that uses the type
- `api/datasets.py:24` [correctness] — Mutable default value on Pydantic field data={} 
- `main.py:111` [correctness] — WebSocket handler conflates connection errors with auth failures and swallows them silently
- `repositories/dataset.py:40` [correctness] — _entities_to_tree silently drops hierarchy when a child precedes its parent
- `ui/routes/admin.py:125` [correctness] — datetime.utcnow() is deprecated
- `ui/routes/auth.py:189` [correctness] — Bare except in refresh_access_token swallows errors without logging
- `ui/routes/auth.py:46` [correctness] — Generic httpx errors not caught in get_oidc_config
- `ui/routes/dataset.py:1264` [correctness] — add_dataset_member does not scope the target user to the dataset tenant
- `ui/spec_builder/access.py:137` [correctness] — Redundant/meaningless User join in can_edit_spec query
- `ui/spec_builder/routes/comment_routes.py:89` [correctness] — add_spec_comment accepts empty/whitespace-only content
- `ui/spec_builder_helpers.py:109` [correctness] — spec_to_yaml mutates the global yaml representer registry as a side effect
- `websocket/__init__.py:22` [correctness] — Naive local-time datetimes used for timestamps in a multi-instance system
- `auth/__init__.py:170` [dead-code] — Unused backwards-compatibility aliases KeycloakAuth and get_keycloak_auth
- `auth/__init__.py:216` [dead-code] — get_current_user_optional and get_oidc_auth dependencies are unused
- `models/mixins.py:61` [dead-code] — SoftDeleteMixin.restore() is never called
- `ui/dependencies.py:268` [dead-code] — Unused CSRFValidationError and DatasetNotFoundError classes
- `ui/dependencies.py:87` [dead-code] — Unused unauthorized_response and get_dataset_by_id
- `ui/routes/table.py:31` [dead-code] — _handle_primitive_list_row has unused parameters dataset_id, nested_type, state
- `ui/routes/table.py:122` [dead-code] — _get_default_values ignores parent_node beyond entity_type; parent_node param partially redundant
- `ui/routes/table.py:238` [dead-code] — Unused 'request: Request' parameter in handlers that never read the request
- `ui/services/entity_service.py:511` [dead-code] — user_id / created_by_id version tracking is never exercised because no caller passes user_id
- `ui/spec_builder/forms.py:107` [dead-code] — ProfileMetadataFormData is never used
- `ui/spec_builder/state.py:229` [dead-code] — Unused SpecBuilderState helper methods
- `ui/services/entity_service.py:355` [design] — _get_validation_warnings validates partial update payloads against the full model, then patches around false positives
- `ui/spec_builder/state.py:98` [design] — Manual reimplementation of Pydantic v2 (de)serialization
- `auth/__init__.py:28` [naming] — keycloak_id property docstring claims 'backwards compatibility' but it is the primary accessor
- `ui/routes/auth.py:24` [typing] — _oidc_config typed dict[str, str] but discovery doc has non-str values
- `ui/routes/dataset.py:615` [typing] — _build_dataset_context types session as Any instead of AsyncSession
- `ui/routes/ontology_api.py:131` [typing] — result dict inferred as str|bool then assigned list/str values

## Appendix — refuted findings

Reported by a reviewer but refuted on verification (kept for the record):

- ui/explore_routes.py:221 User lookup by keycloak_id is not tenant-scoped and can raise/return wrong tenant's user
- ui/routes/table.py:706 field_name / nested_type interpolated into HTML without escaping
- ui/routes/auth.py:130 access_token may be None when set on cookie
- ui/services/entity_service.py:115 ensure_state does not normalize state.profile to facade's profile for custom specs
- ui/spec_builder/routes/member_routes.py:81 add_spec_member looks up user by email without tenant scoping (cross-tenant leak + crash)
