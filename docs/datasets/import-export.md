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

### From a public repository

A dataset whose profile has a matching public repository can be filled from that repository directly. While the dataset is still empty, the sidebar shows a field asking for the identifier the repository uses:

| Profile | Control | What to enter |
|---------|---------|---------------|
| `ena` | Import ENA accession | An ENA accession, e.g. `PRJEB1234` |
| `pride` | Import PRIDE project | A ProteomeXchange accession, e.g. `PXD000001` |
| `metabolights` | Import MetaboLights study | A study accession, e.g. `MTBLS1` |
| `miappe` | Import BrAPI server | A BrAPI v2 server URL |

The hub fetches the public metadata and builds the dataset's entities from it. Metadata only: data files are referenced by name, never downloaded.

While the fetch runs, the control shows an *Importing* indicator and its button is disabled, so a second click cannot start a second import. The fetch runs on a worker thread, not on the request loop: an archive that answers slowly holds up that one import, not every other page the hub is serving. Each importer gives up after its own timeout (30 seconds for ENA) and the control then reports the failure in place.

The control appears only while the dataset has no entities, and the import is refused if any exist, because it replaces the whole entity tree rather than merging into it. To pull a repository record into a dataset you have already started, create a new dataset for it instead.

Which repositories are on offer comes from metaseed's adapter registry, so the list above grows when metaseed adds an importer, without a hub change.

## Exporting

Open a dataset and click **Export** in the sidebar to download it.

### Excel

The Excel export produces one worksheet per entity type (for example *Investigation*, *Study*, *Assay*). Column headers match the entity field names, and nested entities are flattened into their own worksheets. The file name is derived from the dataset's root entity.

| Direction | Format | Extension |
|-----------|--------|-----------|
| Export | Excel | `.xlsx` |
| Export | JSON | `.json` |

### Repository submission formats

A dataset whose profile has a repository exporter shows an extra download button per format, again from metaseed's registry. A PRIDE dataset offers one **PRIDE submission** download: a zip holding both `submission.px` and the SDRF sample table, since a ProteomeXchange submission consists of the two together. The SDRF is omitted when the dataset has no samples rather than shipping an empty table.
