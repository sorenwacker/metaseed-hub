# Changelog

All notable changes to this project will be documented in this file.

## [0.26.0] - 2026-08-05

### Changed
- Require metaseed `>=0.29.0`, which brings the SEEK export capability to the
  hub: provisioning Sample Types and syncing a dataset to a SEEK instance, the
  browsable "what will be created" preview on the SEEK page, remote data-file
  sync, per-version provisioning, and the `seek-ready-template` built-in
  profile. The lockfile is updated so the deploy installs metaseed 0.29.0.

## [0.25.0] - 2026-08-04

### Changed
- Consume the metaseed where the built-in SEEK profile is named `seek`.

### Fixed
- A dataset that will not open says why, instead of failing silently.

## [0.24.0] – [0.24.1] - 2026-08-04

### Fixed
- Dataset sharing across accounts, specification creation, and the forked field
  form. An invitee was looked up within the inviter's tenant, so sharing with a
  user who had already signed in still claimed they must log in first.
- A specification is titled by its name rather than the slug it is stored under.
- A draft stays saveable when another draft holds its spec's name.

### Changed
- Require the metaseed that stops a half-filled row being refused.
- Stop re-testing on a tag whose commit was already tested.

## [0.23.0] - 2026-08-01

### Added
- The DCAT property and spec advice are settable in the browser, not only over
  MCP.

## [0.22.0] - 2026-08-01

### Added
- Agents can declare which field identifies an entity, not only its type.

### Fixed
- A browser edit that would delete unloaded entities is refused, matching the
  agent path.

## [0.21.0] – [0.21.1] - 2026-08-01

### Fixed
- Entities that could not be loaded are reported rather than dropped out of
  sight, and an edit that would delete them is refused.

### Changed
- Use metaseed's public model attribute; a private one can be renamed in a patch
  release.
- MCP tests split by tool family, with the file-size rule gated so they cannot
  drift.

## [0.20.0] - 2026-07-31

### Added
- A version bump that hides breaking changes is refused, and datasets are
  stamped with the spec version they were authored against.

## [0.19.0] - 2026-07-31

### Fixed
- Hub spec drafts can express entity relationships. The instructions promised an
  `items=` parameter the tool did not have, so agents could only build flat
  specs.

## [0.18.0] – [0.18.4] - 2026-07-31

### Added
- Agents can correct specs, create hierarchies in one call, look up ontology
  terms over MCP, and bring external YAML specs and cloned profiles in as
  drafts.

### Changed
- The hub reuses metaseed's spec-builder core instead of maintaining a fork, and
  `metaseed.ui` imports are confined to one boundary module with graph and export
  rebuilt on the public API.
- The spec builder is called Builder everywhere; three templates had named one
  destination three ways.
- The `mcp` dependency the code imports directly is declared, bounded below 2.x.

## [0.17.0] – [0.17.3] - 2026-07-30

### Fixed
- Dataset load failures are surfaced instead of silently saving empty state.
- Error output is escaped in HTML fragments; dataset import and editing defects
  fixed.
- Malformed spec-builder input is rejected rather than erroring, and spec draft
  roles are enforced.
- Reference edges name both connected fields; entity-only edges hid the join
  columns.

### Changed
- Datasets load and save through the facade so cache and storage cannot diverge.

## [0.16.0] – [0.16.6] - 2026-07-29

### Added
- Matomo analytics, self-hosted and cookieless. Only the tracker endpoints
  (`matomo.php`, `matomo.js`) are exposed publicly — never the installer or admin
  UI, which is reachable through an SSH tunnel only.
- Landing-page SEO metadata and `robots.txt`.

### Fixed
- The privacy page discloses analytics and its fabricated claims were removed;
  the legal basis is corrected to public task, since a free university tool is
  not a contract.

## [0.15.0] – [0.15.1] - 2026-07-28

### Added
- Agents can populate data and build specs, not only replace whole datasets.
- Per-user authored spec counts in the admin directory.

### Fixed
- Cancel on the publish dialog cancels.

## [0.14.0] – [0.14.5] - 2026-07-28

### Added
- MCP served at `/hub/mcp`, so an agent works as one hub user, reachable at the
  address it is documented as, with what it can destroy bounded.
- Token authentication for scripts, not only a browser session.
- What is missing on save is reported from the spec's own rules.

### Fixed
- A dataset can be created from a published spec at all, and the unpublish
  form's CSRF token is accepted so the button works.

## [0.13.0] - 2026-07-27

### Changed
- Consume metaseed 0.19.0, where a PRIDE import is a tree.

### Fixed
- An import that found nothing is refused rather than reported as success.

## [0.12.0] - 2026-07-27

### Added
- An empty dataset can be filled from the repository its profile came from.

## [0.11.0] - 2026-07-27

### Changed
- Consume metaseed 0.18.0 with the `dcat` extra, so the DCAT record is offered.

## [0.10.0] - 2026-07-27

### Added
- Unhandled errors are recorded and shown in the admin dashboard, along with
  per-user dataset counts and last sign-in.

### Fixed
- A spec-draft save that would overwrite a newer edit is refused, and a restore
  that cannot change anything is no longer offered.

## [0.9.0] - 2026-07-27

### Added
- Database backups on a timer with tiered retention.

### Changed
- Datasets serialize via `MetaseedClient`, removing the duplicate serializer, and
  entity rows write through the facade rather than only the cache.

## [0.8.0] - 2026-07-25

### Added
- An app-wide CSRF guard, a SAST gate, and self-hosted JS; spec-builder CSRF gaps
  from a second review closed.

## [0.7.0] - 2026-07-25

### Changed
- Security review hardening.

## [0.6.0] – [0.6.1] - 2026-07-25

### Added
- metaseed adapter exports surfaced in the dataset UI (requires metaseed
  >= 0.16.0), with a browser end-to-end test.

## [0.5.0] - 2026-07-24

The first release after the changelog fell out of maintenance; it carries a
month of work. See git history between `v0.4.x` and `v0.5.0` for the full
detail.

## [0.4.65] - 2026-06-23

### Added
- The admin dashboard shows a security warning when the application is running with the default development `SECRET_KEY`

## [0.4.64] - 2026-06-23

### Added
- CSRF tokens are signed with the application `secret_key` (HMAC), so the server only accepts cookies it issued; this hardens the double-submit check against cookie fixation and forgery

### Removed
- Removed the non-functional team chat: messages were never persisted or broadcast. The route, template, model, and the unused `chat_messages` table are dropped; discussion is served by comments

## [0.4.63] - 2026-06-23

### Fixed
- Restored the `secret_key` setting so the `SECRET_KEY` provided by the deployment environment is accepted; its earlier removal made settings loading reject the variable, which aborted the auto-deploy at the migration step

## [0.4.62] - 2026-06-23

### Changed
- Updated metaseed to v0.10.0

## [0.4.61] - 2026-06-23

### Fixed
- CI runs the test suite against a PostgreSQL service so database-backed tests execute instead of erroring on a missing connection
- CI runs on version tag pushes so a release tag fails visibly when the suite is broken

## [0.4.60] - 2026-06-22

### Fixed
- Multi-tenant isolation: table mutations, dataset member management, and spec-comment reactions are now scoped to the caller's access
- Soft-deleted datasets are excluded from the shared access helper, the mutation path, and draft publish
- Stored XSS in entity validation output from unescaped user-derived labels and messages
- WebSocket Redis pub/sub: serialized shared-connection access and stopped the listener spinning before the first subscription
- CSRF validation on dataset member-management and entity-validate routes
- Field renames are validated like field creation, rejecting invalid and duplicate names
- Auth callback rejects a missing access token instead of setting a broken session cookie

### Changed
- Routed tenant/user provisioning, page rendering, and form coercion through their canonical helpers
- Removed dead auth, config, and form surface

## [0.4.58] - 2026-06-15

### Added
- GitHub star count for the hub repository in the footer

### Fixed
- Keycloak user sync returning 403 by authenticating as the master-realm admin

## [0.4.57] - 2026-06-15

### Changed
- Updated metaseed to v0.8.1

## [0.4.53] - 2026-06-10

### Added
- Pre-push hook that runs tests before pushing
- Support for `ontologies` field in spec builder
- Auto-convert spec names to lowercase slug format

### Fixed
- Draft spec profile case mismatch causing "Version not found" errors
- Dataset name field width in create dataset form
- Button layout in dataset creation UI

### Changed
- Updated metaseed to v0.7.7

## [0.4.52] - 2026-06-08

### Removed
- Non-functional export buttons from explorer

## [0.4.51] - 2026-06-07

### Added
- Configurable OAuth scope for different IdPs

## [0.4.50] - 2026-06-06

### Fixed
- Create dataset versions when saving via EntityService
- Ensure all save paths create version history

## [0.4.40-0.4.49] - 2026-05-26 to 2026-06-05

### Added
- Admin role configuration for SRAM
- Version display on login page
- Comment pagination and compact UI

### Fixed
- Spec export download and session timeout
- Date serialization to ISO strings for JSONB storage
- Pydantic URL type handling in JSON serialization
- Modal dialog text colors and input styling

### Changed
- Updated metaseed to v0.7.4

## [0.4.30-0.4.39] - 2026-05-20 to 2026-05-25

### Added
- Field reordering in spec builder
- Ontology demo spec
- Single-select ontology modal fix

### Fixed
- Redirect to home after dataset delete
- Use MetaseedClient.load() for graph operations
- Skip validation on entity save, show warnings instead

### Changed
- Migrated to MetaseedClient public API
- Removed global dataset_states cache

## [0.4.20-0.4.29] - 2026-05-15 to 2026-05-19

### Fixed
- Preserve nested entity values when saving
- Skip 'Field required' warning for nested fields with partial data
- Use cached state for draft specs

### Changed
- Updated metaseed to v0.3.9

## [0.4.10-0.4.19] - 2026-05-10 to 2026-05-14

### Added
- YAML copy button
- Show shared specs in Create Dataset

### Fixed
- Load draft specs from database for dataset mutations
- Disable cache for draft specs
- Preserve entities even when validation fails

## [0.4.0-0.4.9] - 2026-05-01 to 2026-05-09

### Added
- GitHub issues link to footer
- OLS ontology API integration
- Comprehensive RBAC tests for datasets and specs

### Fixed
- Show shared datasets on home page
- Grant dataset access via DatasetMember sharing

### Refactored
- Split spec_builder router into focused route modules
- Extract auth dependency and add type aliases

## [0.3.0-0.3.9] - 2026-04-15 to 2026-04-30

### Added
- Threaded comments with reactions
- Excel template export/import workflow
- Auto-versioning for datasets on save
- Detailed git-style diff view for version history
- Delete button to dataset sidebar
- User profile page showing SRAM info

### Fixed
- State caching to prevent data loss on save
- Refresh root type buttons on entity create/delete

### Refactored
- Renamed Project to Dataset throughout codebase
- Simplified dataset UI and navigation

## [0.2.0-0.2.9] - 2026-04-01 to 2026-04-14

### Added
- Explorer tool with dark header navigation
- Workspace members and spec draft integration
- Delete button for spec drafts
- Import/export and single entity field support
- DatabaseDatasetRepository for metaseed DI integration

### Fixed
- Footer visibility and layout
- Version info display
- Full-page layout handling

### Changed
- Initial metaseed integration (v0.2.0-0.2.4)
