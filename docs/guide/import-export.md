# Import and Export

Metaseed Hub supports exporting project data to standard formats.

## Export to Excel

Export all entities in a project to an Excel workbook.

**How to export:**

1. Open a project
2. Click the **Export** button in the toolbar
3. The browser downloads an `.xlsx` file

**Output format:**

- One worksheet per entity type (Investigation, Study, Assay, etc.)
- Column headers match entity field names
- Nested entities are flattened to separate worksheets

**Implementation:**

Uses `metaseed.ui.services.export`:

- `export_to_bytes(state)` - generates Excel workbook as bytes
- `generate_filename(state)` - creates filename from root entity

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/hub/projects/{id}/export` | GET | Download Excel file |

## Supported Formats

| Direction | Format | File Extension |
|-----------|--------|----------------|
| Export | Excel | `.xlsx` |
