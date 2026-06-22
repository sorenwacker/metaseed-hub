# Changelog

All notable changes to this project will be documented in this file.

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
