# Codebase Review

Date: 2026-06-22. Reviewed source root `src/metaseed_hub` (53 source files, ~11,800 LOC) with a per-module multi-agent pass across 19 module groups; every high/medium finding was adversarially verified by an independent agent before inclusion, which may correct the original severity. The metaseed dependency was at v0.9.1 during the review.

This supersedes the 2026-06-20 review. That cycle remediated a tenant-isolation theme (branch `review-260620-tenant-isolation`) and a soft-delete consistency theme (branch `review-260620-soft-delete`) for the specific findings it listed. This pass surfaces additional, previously-uncovered instances of those same two classes plus a new stored-XSS finding and a websocket concurrency finding; none are carry-overs whose fix regressed.

## Baseline gates

The project's own canonical commands (Makefile / pre-commit), run before the review.

| Gate | Command | Result |
|------|---------|--------|
| Lint | `uv run ruff check src tests` | Pass |
| Types | `uv run mypy src` (strict) | Pass, 53 files |
| Dead code | `uv run vulture src/ vulture_whitelist.py --min-confidence=80` | Pass, no hits |
| File size | 1000 LOC project rule | Pass, largest `ui/helpers.py` at 899 LOC |
| Tests | `uv run python -m pytest` | 157 passed, 1 skipped, 69 errors |

The 69 test errors are not code defects: every one is the same fixture failure in `tests/conftest.py:28`, an `OSError` from the async engine connecting to `postgresql+asyncpg://...@localhost:7432/metaseed_hub_test`. No Postgres is running in this environment. Start it with `make up` to get a clean run. No `TODO`/`FIXME` markers remain in the source.

## Summary

- Total findings: 68 (confirmed 25, refuted 1, unverified low-severity 42).
- Confirmed by corrected severity: high 6, medium 12, low 7.
- Confirmed by category: correctness 12, consistency 8, dead-code 4, design 1.

Recurring themes:

- **Authorization / multi-tenant isolation gaps (the dominant theme).** Three confirmed findings let a user reach or mutate data outside their scope: every mutation route in `table.py` loads its dataset by id alone with no tenant/membership check (cross-tenant IDOR write/delete); the dataset member-management routes authorize any member including a VIEWER, so a VIEWER can promote themselves to OWNER; and `react_to_spec_comment` toggles a reaction on a comment without confirming it belongs to the URL draft. These are the same class the 2026-06-20 tenant-isolation branch fixed elsewhere, in code paths that branch did not touch.
- **Soft-delete inconsistency.** The two canonical dataset access helpers, `get_dataset_for_user` and `get_dataset_state_for_mutation`, both omit the `deleted_at IS NULL` filter every sibling query applies, so a soft-deleted dataset stays readable and mutable through ~40 routes and all six `table.py` mutation endpoints. `publish_draft`'s source-spec lookup omits the same filter. Again, the same class the soft-delete branch fixed in other queries.
- **Stored XSS.** `dataset_validate` hand-builds HTML with f-strings and interpolates user-controlled entity labels and validation messages without escaping, while its sibling `dataset_chat` in the same file escapes for exactly this reason.
- **Shared-helper divergence.** Inline reimplementations of canonical helpers that drift from or drop guards the original has: two copies of tenant/user get-or-create (`ensure_tenant_and_user`), a duplicated CSRF token helper and cookie constant, two policy pages bypassing `render_template`, and inline numeric form coercion that drops the `ValueError` guard `forms.py` has.
- **Inconsistent CSRF posture.** Several mutating routes skip the `validate_csrf_or_error` check their siblings enforce (dataset member routes; spec-builder mutating routes; the entity validate POST).

## Remediation status

All 25 confirmed findings are remediated on branch `review-260620-soft-delete`, in seven atomic commits grouped by theme:

- **Soft-delete (H2, H3, M5, M6).** `get_dataset_for_user` and `get_dataset_state_for_mutation` now filter `deleted_at IS NULL` (the mutation dependency routes through the access helper, so H3/H4/M6 share one fix); `publish_draft` excludes soft-deleted source specs.
- **Authorization / IDOR (H4, H6, M8, L4).** Table mutations now load the dataset through the access-checked helper; a new `require_dataset_owner` gates the dataset member routes (denying VIEWERs); `react_to_spec_comment` is scoped to the URL draft; `dataset_chat` performs an access check.
- **Stored XSS (H1).** `dataset_validate` escapes all user-derived values.
- **WebSocket concurrency (H5, L6).** A `PubSub` lock serializes the listener read against subscribe/unsubscribe; the connection handler logs unexpected errors.
- **CSRF posture (M3, L2).** The member routes and the entity-validate POST validate CSRF.
- **Shared-helper divergence (M1, M2, M4, M7, L1).** Privacy/AUP pages render through `render_template`; `app.home` and `get_user_context` use `ensure_tenant_and_user`; `table.py` uses `parse_form_field`; the duplicate CSRF helper/constant is removed.
- **Dead code and minor (M9, M10, M11, M12, L3, L5, L7).** Removed `get_current_user_optional`/`security_optional`, `secret_key`, `get_dataset_by_id`, `ProfileMetadataFormData`; `update_field` validates names; `auth_callback` guards a missing access token.

Tests were added or extended (`test_soft_delete`, `test_tenant_isolation`, `test_websocket`, `test_csrf`, `test_forms`); the database-backed ones run under `make up`. Two items have no dedicated automated test: the M5 publish path (no route-level harness exists) and the H1 escaping (the validate path needs a full facade harness) — both are one-line, localized changes verified against the code. L5 was resolved by removing the never-wired `user_id` parameter rather than adding authorship tracking; wiring authorship is a separate, deliberate feature.

## Confirmed findings

Severity is the adversarial verifier's corrected value; the reviewer's original is noted where it changed. All findings below are remediated (see Remediation status).

### High

#### H1 — `dataset_validate` interpolates user data into HTML without escaping, stored XSS (`ui/routes/dataset/editor.py:332`, consistency)

`dataset_validate` builds raw HTML via f-strings, interpolating `err["entity_type"]`, `err["label"]`, the field path, and `field_err["message"]` directly (lines 323, 336, 340, 345, 346) and returning it through `HTMLResponse`, which does not auto-escape. `node.label` is derived from user-entered entity field values (`helpers.py:318-320` sets it via `get_label_from_values`), so a user who saves an entity with name/title `<script>...</script>` stores a payload that executes in any user's browser when the validation panel opens. The sibling `dataset_chat` handler in the same file (lines 447-450) imports `html` and escapes user content "to prevent XSS" for exactly this reason.

Fix: escape all user-derived values with `html.escape(...)` before interpolating, mirroring the chat handler, or render via the Jinja layer which auto-escapes.

#### H2 — `get_dataset_for_user` omits `deleted_at IS NULL`, exposing soft-deleted datasets (`ui/dependencies.py:236`, correctness)

The canonical access helper loads `select(Dataset).where(Dataset.id == dataset_id)` with no soft-delete predicate. `Dataset` uses `SoftDeleteMixin`, and there is no global SQLAlchemy filter (no `with_loader_criteria`/`do_orm_execute` listener anywhere in `src/`), so exclusion must be per-query. Every sibling query filters soft-deleted rows (`repositories/dataset.py`, `api/datasets.py`, `ui/app.py`, `spec_builder/routes/draft_routes.py:112`). Because this helper backs ~40 read/mutation routes, a soft-deleted dataset stays fully readable and mutable through all of them.

Fix: add `Dataset.deleted_at.is_(None)` to the where clause and treat a soft-deleted row as 404.

#### H3 — `get_dataset_state_for_mutation` omits `deleted_at IS NULL` on the mutation path (`ui/dependencies.py:320`, correctness)

The dependency that guards every `table.py` mutation/delete endpoint (used at `table.py` lines 244, 391, 456, 508, 557, 663) loads the dataset by id with no soft-delete filter and 404s only on a missing row. A soft-deleted dataset can therefore still be mutated and have its `AppState` facade rebuilt. (H3 and the medium "table mutations operate on soft-deleted datasets" are the same root cause seen from two reviewers; fixing this query closes both.)

Fix: add `Dataset.deleted_at.is_(None)` so soft-deleted datasets return 404.

#### H4 — All `table.py` mutation routes lack a tenant/membership check, cross-tenant IDOR (`ui/routes/table.py:244`, correctness)

All six mutation routes (`add_table_row`, `update_table_cell`, `update_primitive_list_item`, `delete_primitive_list_item`, `update_single_entity_field`, `delete_single_entity_field`) depend solely on `get_dataset_state_for_mutation`, which validates auth + CSRF then loads the dataset by id alone — no tenant ownership or `DatasetMember` check. `table.py` is the only consumer of that dependency. The sibling `entity.py` routes use `get_dataset_for_user`, which enforces tenant ownership or membership. As written, any authenticated user from any tenant can add/edit/delete inline-table rows and nested entities on any dataset by knowing its id.

Fix: have `get_dataset_state_for_mutation` perform the same access check as `get_dataset_for_user` (tenant ownership or `DatasetMember`), or call it internally.

#### H5 — Single redis `PubSub` mutated from join/leave while the listener reads it concurrently (`websocket/__init__.py:86`, correctness)

`connect_redis` starts `_listen` as a background task looping on `self._pubsub.get_message(...)` while `join_room` (line 190) and `leave_room` (line 235) call `subscribe`/`unsubscribe` on the *same* `PubSub` object from the per-connection handlers. Verified against the installed redis-py 6.4.0: these share one connection/parser and neither path takes a lock (the `PubSub._lock` only guards `aclose()`), so an in-flight read can interleave with a subscribe/unsubscribe write and corrupt the RESP stream. The `timeout=1.0` blocking read is the exact await window. The tests use a `FakeRedis` that never runs the real listener concurrently with subscribe, so they miss it; `_listen` only logs exceptions, so it surfaces as intermittent listener crashes / missed broadcasts.

Fix: serialize all `PubSub` operations behind an `asyncio.Lock`, or subscribe once to an instance-wide pattern channel (`project:*:messages`) at connect time and filter by `project_id` in `_dispatch_local` instead of subscribing/unsubscribing per room.

#### H6 — Member-management routes authorize any member, not just owners (`ui/routes/dataset/members.py:51`, design)

`add_dataset_member`, `update_dataset_member_role`, and `remove_dataset_member` authorize only via `get_dataset_for_user`, which succeeds for any `DatasetMember` of any role. The PATCH route then does `member.role = DatasetRole(role)` with no restriction, so a user shared as VIEWER can add members, remove members, and promote themselves (or anyone) to OWNER. The canonical sibling `spec_builder/routes/member_routes.py` gates every add/update/remove with `require_draft_owner`.

Fix: add an owner-level check (tenant ownership or `DatasetRole.OWNER`) before mutating membership, mirroring `require_draft_owner`.

### Medium

#### M1 — privacy/aup routes bypass `render_template`, dropping CSRF cookie and version footer (`ui/app.py:305`, consistency)

`privacy_policy` (line 306) and `acceptable_use_policy` (line 315) call `templates.TemplateResponse(...)` directly instead of the canonical `render_template`, which injects `csrf_token`, seeds the CSRF cookie when missing, and injects `version_info`. Both templates reference these (guarded with `if`, so no crash), so the pages never seed the CSRF cookie and render the footer as the fallback `dev`/`unknown`.

Fix: render both via `render_template(request=request, name=..., context={"user": user})`.

#### M2 — Local `get_or_create_tenant` reimplements `ensure_tenant_and_user` and skips User creation (`ui/app.py:201`, consistency)

The nested helper only get-or-creates the `Tenant`; the canonical `ensure_tenant_and_user` (`dependencies.py:130`) creates both `Tenant` and `User` in one transaction. `home()` then looks up `db_user` separately and silently treats a missing `User` as "no shared datasets/specs", so a freshly onboarded user sees an incomplete home page. It also duplicates the `keycloak_id[:8]` slug derivation centralized elsewhere.

Fix: replace the nested helper and the manual lookup with one `ensure_tenant_and_user(session, user)` call returning `(tenant, db_user)`.

#### M3 — Member mutating routes skip CSRF validation their siblings enforce (`ui/routes/dataset/members.py:51`, consistency)

`add_dataset_member`, `update_dataset_member_role`, and `remove_dataset_member` commit DB writes with no `validate_csrf_or_error` call; the module does not import the CSRF helpers at all. Sibling mutating routes do validate (`comments.py` lines 112-115/147-150/186-189, `versions.py` 271-274, `crud.py` `dataset_delete`). CSRF is enforced strictly per-route — there is no middleware — so this is a real gap. The member HTMX forms (`dataset.html`) are live, browser-driven endpoints.

Fix: wrap the three handlers with `validate_csrf_or_error(request)` / `csrf_error_response()` and ensure the member forms submit the token.

#### M4 — `get_user_context` inlines tenant/user get-or-create instead of `ensure_tenant_and_user` (`ui/spec_builder/access.py:71`, consistency)

Lines 71-102 reimplement the canonical helper and drift: two separate `commit()`+`refresh()` round-trips instead of one `flush()`+`commit()`, and the tenant display name uses `token_user.name or token_user.email` (full email) versus the canonical `user.name or user.email.split("@")[0]` (local part) — so a new tenant's display name depends on which path created it. `access.py` already imports from `dependencies.py`, so reuse is feasible.

Fix: call the canonical `ensure_tenant_and_user`, or extract a shared helper both call.

#### M5 — `publish_draft` source-spec lookup omits the soft-delete filter (`ui/spec_builder/routes/draft_routes.py:185`, consistency)

`select(Spec).where(Spec.id == draft.source_spec_id)` has no `Spec.deleted_at.is_(None)` guard; every other `Spec` query applies it (`draft_routes.py:251/287`, `access.py:127`, `list_routes.py:78`). A draft derived from a since-deleted spec still finds the soft-deleted row and runs `can_edit_spec` against it. Effect is partly mitigated (`can_edit_spec` re-queries with the filter and returns False → 403), but the behavior diverges from the soft-delete intent (it should publish as a new spec).

Fix: `select(Spec).where(Spec.id == draft.source_spec_id, Spec.deleted_at.is_(None))`.

#### M6 — Table mutations operate on soft-deleted datasets (`ui/routes/table.py:244`, correctness; reviewer high → medium)

Same root cause as H3: `get_dataset_state_for_mutation` omits `deleted_at IS NULL`, so all six `table.py` endpoints can mutate a soft-deleted dataset. Downgraded from high because exploitation requires the dataset to already be soft-deleted and the actor must still pass auth + CSRF; it is a soft-delete-resurrection bug rather than an isolation bypass. Fixed by the H3 fix.

#### M7 — Unhandled `ValueError` on `int()`/`float()` form coercion can 500 the request (`ui/routes/table.py:426`, correctness)

`update_table_cell` (lines 426-429) and `update_single_entity_field` (lines 603-606) call bare `int()`/`float()` on raw form values; `update_single_entity_field`'s try block catches only `AttributeError`. HTML `type=number` is client-side only and trivially bypassed, so a non-numeric submit raises an uncaught `ValueError` → 500 (no handler covers it). The canonical `parse_form_field`/`extract_entity_values` in `forms.py` wrap the conversion in `try/except ValueError` with a raw-string fallback.

Fix: reuse `parse_form_field`/`extract_entity_values` instead of re-implementing the coercion.

#### M8 — `react_to_spec_comment` does not confirm the comment belongs to the URL draft, IDOR (`ui/spec_builder/routes/comment_routes.py:127`, correctness; reviewer high → medium)

The handler calls `require_draft_access(draft_id)` then operates on `comment_id` directly, never confirming the comment belongs to `draft_id`. A member of draft A can pass `draft_id=A` with a `comment_id` from draft B and toggle/add a reaction on the foreign comment. The sibling `delete_spec_comment` scopes correctly (`SpecComment.spec_draft_id == draft_id`), and the canonical `react_to_comment` in `dataset/comments.py:203-207` loads the comment with `Comment.dataset_id == dataset_id` and 404s otherwise. Downgraded from high because the only achievable action is a like/dislike write on a foreign comment — no content is leaked, nothing is deleted or modified.

Fix: load the comment scoped to the draft (`SpecComment.id == comment_id, SpecComment.spec_draft_id == draft_id`) and 404 if None.

#### M9 — `update_field` accepts invalid and duplicate field names that `add_field` rejects (`ui/spec_builder/routes/field_routes.py:181`, correctness)

`add_field` validates with `validate_field_name(name)` and rejects names already on the entity; `update_field` does neither, directly assigning `field.name = form_data.name.strip()`. Verified that `FieldSpec` uses `ConfigDict(extra="forbid")` without `validate_assignment`, so the assignment bypasses all validation. A user can rename a field to an empty/invalid value or collide with another field, producing two fields with identical names that persist into the saved spec.

Fix: run `validate_field_name(name)` and a duplicate check (excluding `idx`) in `update_field`, returning the entity_editor partial with an error before mutating.

#### M10 — `get_current_user_optional` / `security_optional` are never used (`auth/__init__.py:234`, dead-code)

Both are defined and `get_current_user_optional` is exported in `__all__`, but a tree-wide grep finds no importer or caller. Authenticated routes use the mandatory `get_current_user`; UI routes use `verify_token` directly. This is misleading exported public surface.

Fix: remove both and the `__all__` entry, or wire them into optional-auth routes if intended.

#### M11 — Unused `secret_key` setting (`config.py:39`, dead-code)

`Settings.secret_key` is declared but never read (`.secret_key` has zero attribute-access hits). There is no `SessionMiddleware`, and CSRF uses a random `secrets.token_urlsafe(32)` token validated by double-submit cookie comparison — no HMAC keyed by this secret. The setting signs/protects nothing and falsely implies signed sessions/CSRF.

Fix: remove it, or wire it into an actual session/CSRF signing path and document what it secures.

#### M12 — `get_dataset_by_id` is never used (`ui/dependencies.py:98`, dead-code)

Defined at lines 98-112 with zero references; its docstring steers callers to `get_dataset_for_user`. It queries by id with no tenant scoping and no `deleted_at` filter, so on this hardening branch it is a latent IDOR/soft-delete footgun if anyone wires it up.

Fix: remove it, or, if kept as a primitive, add tenant + soft-delete guards and a test.

### Low

#### L1 — CSRF helper and cookie constant duplicated in `dependencies.py` (`ui/helpers.py:109`, consistency; reviewer medium → low)

`helpers.py` defines the canonical `CSRF_TOKEN_COOKIE` and `get_or_create_csrf_token`; `dependencies.py` redefines both verbatim (including the `len(token) == 43` guard) while already importing other helpers from `helpers.py`. Every live caller uses the `helpers.py` version; the `dependencies.py` copy is unused dead duplication that can silently drift. Fix: delete the duplicates and import from `helpers.py`.

#### L2 — `dataset_entity_validate` POST omits CSRF validation its sibling performs (`ui/routes/entity.py:160`, consistency; reviewer medium → low)

The validate POST does not call `validate_csrf_or_error`, unlike `dataset_validate` and the create/delete handlers in the same file. The endpoint is read-only (no persistence), so it is a consistency nit rather than a security gap. Fix: add the same CSRF block, or document why this read-only POST skips it.

#### L3 — `auth_callback` sets the access-token cookie without verifying the token is present (`ui/routes/auth.py:130`, correctness; reviewer medium → low)

After the token exchange only `status_code != 200` is checked; `access_token = tokens.get("access_token")` can be `None` and is passed straight to `set_cookie`. Verified that Starlette 0.45.3 does not raise — it serializes the literal cookie value `None`, leaving a silently broken session. The `refresh_token` path is correctly guarded; this one is not. Only reachable with a non-compliant IdP response, hence low. Fix: `if not access_token: return RedirectResponse("/hub/?error=token_exchange_failed", 302)` before setting the cookie.

#### L4 — POST `/{dataset_id}/chat` performs no dataset access/membership check (`ui/routes/dataset/editor.py:432`, correctness; reviewer high → low)

`dataset_chat` validates CSRF but never calls `get_dataset_for_user` and takes no `session`, unlike every sibling. Downgraded from high because the handler ignores `dataset_id` entirely — it loads no dataset, reads/writes nothing, and only echoes back the caller's own escaped message. No cross-tenant data flows. Fix: add `session` and call `get_dataset_for_user` for consistency and future-proofing.

#### L5 — `DatasetVersion.created_by_id` is always None; version authorship never recorded (`ui/services/entity_service.py:511`, correctness; reviewer medium → low)

`EntityService.__init__` accepts `user_id` (default None) and `_save_state` writes `created_by_id=self._user_id`, but all five route call sites and all tests construct `EntityService(session, dataset)` without it, so authorship is never populated. The constructor param and its comment imply a feature never exercised. Fix: resolve `CurrentUser` to the DB `User.id` (as `get_dataset_for_user` already does) and pass `user_id=db_user.id`, or drop the parameter.

#### L6 — Bare `except Exception` in `handle_connection` swallows errors with no logging (`websocket/__init__.py:342`, correctness; reviewer medium → low)

The receive loop ends with `except Exception: await self.leave_room(...)` and no logging, so a malformed non-JSON frame (`json.JSONDecodeError` at line 327) silently tears down the session like a normal disconnect. Sibling code logs via `logger.exception(...)`. Outcome is safe (no leak), so low. Fix: log before leaving, and consider catching `JSONDecodeError` separately to return an error frame.

#### L7 — `ProfileMetadataFormData` is dead code (`ui/spec_builder/forms.py:107`, dead-code; reviewer medium → low)

The dataclass is never imported or referenced, while its siblings `FieldFormData` and `ValidationRuleFormData` are consumed by their routes. The matching route `update_profile_metadata` (`draft_routes.py:328`) binds individual `Form()` params instead. Fix: wire it into `update_profile_metadata` to match the sibling pattern, or remove it.

## Refuted

One finding was refuted by the adversarial pass and is not included above:

- `ui/spec_builder/state.py:15` — "Duplicate `spec_to_dict` helper diverges from the one in `spec_builder_helpers.py`."

## Appendix — unverified lower-severity notes

These 42 low-severity findings were not adversarially verified (the workflow verifies high/medium by default). Several reinforce the confirmed themes and are worth folding into the same remediation passes — notably the CSRF and scoping items. Treat as leads, not confirmed defects.

- [consistency] `api/datasets.py:18` — API dataset access uses tenant-owner-only scoping while UI routes also honor `DatasetMember` sharing.
- [design] `api/health.py:31` — `health_check` builds and disposes a throwaway async engine on every request.
- [dead-code] `auth/__init__.py:188` — `KeycloakAuth` / `get_keycloak_auth` backwards-compat aliases are unreferenced.
- [correctness] `auth/__init__.py:193` — `get_oidc_auth` singleton ignores settings after first call.
- [typing] `auth/__init__.py:44` — OIDC config / JWKS type annotations understate nested structure.
- [correctness] `main.py:138` — Possible redundant `websocket.close` after `handle_connection` returns.
- [correctness] `repositories/dataset.py:79` — `_entities_to_tree` silently orphans children that precede their parent in the input list.
- [correctness] `ui/app.py:102` — `TokenRefreshMiddleware` swallows all exceptions from `verify_token`.
- [consistency] `ui/explore_routes.py:247` — Mixed use of `user.sub` and `user.keycloak_id` within the same function.
- [consistency] `ui/explore_routes.py:405` — `get_diff_graph` does not guard empty `profile_tuples` like `explore_compare` does.
- [correctness] `ui/helpers.py:112` — CSRF token validity reduced to a hardcoded length check (43).
- [dead-code] `ui/helpers.py:186` — Unreachable facade-None branch in `deserialize_tree`.
- [correctness] `ui/routes/admin.py:130` — `func.date()` on timezone-aware `created_at` may misgroup activity by UTC vs local day.
- [typing] `ui/routes/auth.py:27` — `get_oidc_config` and `_oidc_config` typed as `dict[str, str]` but hold arbitrary JSON.
- [correctness] `ui/routes/auth.py:189` — `refresh_access_token` swallows all exceptions silently with no logging.
- [dead-code] `ui/routes/auth.py:59` — `request` parameter unused in `auth_login` and `auth_logout`.
- [consistency] `ui/routes/dataset/comments.py:30` — Comment/version/member queries do not confirm the parent dataset is not soft-deleted.
- [dead-code] `ui/routes/dataset/crud.py:186` — Unused `db_user` unpacked in `dataset_import`.
- [correctness] `ui/routes/dataset/crud.py:256` — `dataset_import` never resolves `draft:` profiles, unlike `dataset_create`.
- [consistency] `ui/routes/dataset/crud.py:524` — Operating routes can act on soft-deleted datasets.
- [design] `ui/routes/dataset/crud.py:63` — `version_key` redefined on every profile-loop iteration.
- [correctness] `ui/routes/dataset/editor.py:523` — Import `_type` fallback uses profile name as an entity type and is silently dropped.
- [dead-code] `ui/routes/dataset/editor.py:640` — Trailing "Member Management Routes" section banner with no following content.
- [correctness] `ui/routes/dataset/members.py:126` — Unvalidated role value can raise `ValueError` → 500.
- [dead-code] `ui/routes/dataset/versions.py:28` — Unused `prefix` parameter threaded through `_flatten_tree`.
- [typing] `ui/routes/dataset/versions.py:85` — `_calculate_diff` `has_changes` returns `list|bool` instead of `bool`.
- [dead-code] `ui/routes/table.py:26` — `_handle_primitive_list_row` has three unused parameters.
- [dead-code] `ui/routes/table.py:240` — Unused `request` parameter in `add_table_row` and `delete_single_entity_field`.
- [consistency] `ui/routes/table.py:253` — `logging` imported and logger re-created inside function bodies instead of module level.
- [dead-code] `ui/security.py:49` — `require_csrf` decorator is unused in production code.
- [correctness] `ui/services/entity_service.py:299` — Stale `node_id` silently falls through to entity creation instead of erroring.
- [dead-code] `ui/services/entity_service.py:302` — Unused `entity` assignment in the update branch.
- [consistency] `ui/spec_builder/routes/comment_routes.py:0` — spec_builder mutating routes omit CSRF validation applied by the canonical dataset routes.
- [dead-code] `ui/spec_builder/routes/draft_routes.py:380` — `export_yaml` re-imports `load_state_for_draft` already imported at module level.
- [consistency] `ui/spec_builder/routes/draft_routes.py:157` — `reset_draft` persists state inline instead of using the `save_state_to_draft` helper.
- [consistency] `ui/spec_builder/routes/list_routes.py:56` — Shared-drafts query omits explicit `tenant_id` filter that the owned-drafts query applies.
- [dead-code] `ui/spec_builder/routes/list_routes.py:134` — Defensive `hasattr(spec, 'name')` is always true and adds noise.
- [dead-code] `ui/spec_builder/routes/list_routes.py:91` — `new_spec_form` and `import_spec_form` inject `session` and unpack `user_id` but never use them.
- [consistency] `ui/spec_builder/routes/member_routes.py:86` — `add_spec_member` scopes the email lookup to the caller's tenant rather than the draft's tenant.
- [correctness] `ui/spec_builder/routes/rule_routes.py:150` — `update_validation_rule` coerces numeric form fields inline, raising an unhandled `ValueError` (500) on bad input.
- [consistency] `ui/spec_builder_helpers.py:42` — `TYPE_CHECKING` import block placed in the middle of the module after a function.
- [correctness] `websocket/__init__.py:116` — `_dispatch_local` assumes the channel always has the `a:b:c` shape.
