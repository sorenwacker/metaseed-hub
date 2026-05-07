# Import and Export

Metaseed Hub supports importing and exporting project data using standard formats.

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

## Import ISA-JSON

Import Investigation, Study, and Assay structures from ISA-JSON files.

**How to import:**

1. Open a project
2. Click the **Import** button in the toolbar
3. Select an ISA-JSON file (`.json`)
4. Click **Import**

**Supported structure:**

The importer handles standard ISA-JSON format:

```json
{
  "identifier": "INV-001",
  "title": "My Investigation",
  "studies": [
    {
      "identifier": "STU-001",
      "assays": [...]
    }
  ]
}
```

**Implementation:**

Uses `metaseed.importers.isa.ISAImporter`:

- Parses ISA-JSON structure
- Creates Investigation, Study, Assay entities
- Preserves nested relationships (contacts, protocols, samples)

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/hub/projects/{id}/export` | GET | Download Excel file |
| `/hub/projects/{id}/import` | GET | Show import form |
| `/hub/projects/{id}/import` | POST | Upload and process ISA-JSON |

## Supported Formats

| Direction | Format | File Extension |
|-----------|--------|----------------|
| Export | Excel | `.xlsx` |
| Import | ISA-JSON | `.json` |
