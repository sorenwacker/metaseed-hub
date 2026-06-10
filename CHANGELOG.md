# Changelog

All notable changes to this project will be documented in this file.

## [0.4.53] - 2026-06-10

### Added
- Pre-push hook that runs tests before pushing
- Support for `ontologies` field in spec builder to specify which ontologies to search for ontology_term fields
- Auto-convert spec names to lowercase slug format (e.g., `MySpec` → `my-spec`)

### Fixed
- Draft spec profile case mismatch causing "Version not found" errors
- Dataset name field width in create dataset form
- Button layout in dataset creation UI (greyed out until name entered, "With Example" only shown when example exists)

### Changed
- Updated metaseed dependency to v0.7.7

## [0.4.52] - 2026-06-08

### Removed
- Non-functional export buttons from explorer
