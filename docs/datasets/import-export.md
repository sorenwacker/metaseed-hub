# Importing and exporting

You can create a dataset from an existing file, import data into an open dataset, and export a dataset to a standard format.

## Importing

### When creating a dataset

On the **+ New Dataset** screen, open the **Import File** tab and provide a file. Metaseed Hub reads the entities from the file and creates a dataset from them. Supported inputs include ISA-JSON, YAML, and Excel.

### Into an existing dataset

Open a dataset and use **Import** in the sidebar to load entities from a file into the current dataset.

| Input format | File type |
|--------------|-----------|
| ISA-JSON | `.json` |
| YAML | `.yaml`, `.yml` |
| Excel | `.xlsx` |

!!! note
    Imported data is validated against the dataset's profile. Run **Validate** after importing to review any errors or warnings.

## Exporting

Open a dataset and click **Export** in the sidebar to download it.

### Excel

The Excel export produces one worksheet per entity type (for example *Investigation*, *Study*, *Assay*). Column headers match the entity field names, and nested entities are flattened into their own worksheets. The file name is derived from the dataset's root entity.

| Direction | Format | Extension |
|-----------|--------|-----------|
| Export | Excel | `.xlsx` |
| Export | JSON | `.json` |
