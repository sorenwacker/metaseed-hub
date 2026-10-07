# Importing and exporting

You can create a dataset from an existing file, import data into an open dataset, and export a dataset to a standard format.

## Importing

### When creating a dataset

On the **+ New Dataset** screen, open the **Import File** tab and provide a file. Metaseed Hub reads the entities from the file and creates a dataset from them. Supported inputs include ISA-JSON, YAML, and Excel. The profile list on this tab offers the same choices as the **New** tab — the built-in standards, your drafts and the published specifications you may see — and a dataset imported against a draft or a published specification is bound to it exactly as one created from the **New** tab is.

### Into an existing dataset

Open a dataset and use **Import** in the sidebar to load entities from a file into the current dataset.

| Input format | File type |
|--------------|-----------|
| ISA-JSON | `.json` |
| YAML | `.yaml`, `.yml` |
| Excel | `.xlsx` |

!!! note
    Imported data is validated against the dataset's profile. Run **Validate** after importing to review any errors or warnings.

A file that reads but whose entities cannot be loaded under the chosen profile and version is refused: the form reports it and no dataset is created. The same holds for the example data of a profile when **Create with example** is ticked.

### From a public repository

There are two ways in: the **Import** page, which creates the datasets for you, and the import field of a dataset that is still empty.

#### The Import page

Select **Import** in the header. The page asks for two things: the repository, and the identifiers to fetch, one per line.

| Repository | What to enter | Profile of the new dataset |
|------------|---------------|----------------------------|
| ENA | Accessions, e.g. `PRJEB1234` | `ena` |
| PRIDE | ProteomeXchange accessions, e.g. `PXD000001` | `pride` |
| MetaboLights | Study accessions, e.g. `MTBLS1` | `metabolights` |
| BrAPI | BrAPI v2 server URLs | `miappe` |

Select **Import** and the hub creates one dataset per identifier, in the latest version of the repository's profile. You name nothing: a dataset takes the title its root record carries at the repository, and the identifier where the record has no title. If you already have a dataset of that name, the identifier is appended in parentheses; if that name is taken as well, the record is reported as already imported and nothing is created.

One submission takes up to 20 identifiers. They are fetched one after another, and the page lists each with its outcome as it finishes:

| Outcome | Meaning |
|---------|---------|
| Imported | The dataset exists; the row links to it |
| Nothing to import | The repository answered, and holds no records of the kind the importer reads for this identifier |
| Already imported | You have a dataset for this record under both names described above |
| Failed | The identifier was malformed, or the repository's answer could not be read |
| Not checked | The repository did not answer in time; try again later |

An identifier that fails does not stop the ones after it, and no dataset is created for anything but an **Imported** row. A repeated identifier in one submission is fetched once.

#### Into an empty dataset

A dataset whose profile has a matching public repository can also be filled from that repository directly. While the dataset is still empty, the sidebar shows a field asking for the identifier the repository uses:

| Profile | Control | What to enter |
|---------|---------|---------------|
| `ena` | Import ENA accession | An ENA accession, e.g. `PRJEB1234` |
| `pride` | Import PRIDE project | A ProteomeXchange accession, e.g. `PXD000001` |
| `metabolights` | Import MetaboLights study | A study accession, e.g. `MTBLS1` |
| `miappe` | Import BrAPI server | A BrAPI v2 server URL |

The hub fetches the public metadata and builds the dataset's entities from it. Metadata only: data files are referenced by name, never downloaded.

While the fetch runs, the control shows an *Importing* indicator and its button is disabled, so a second click cannot start a second import. The fetch runs on a worker thread, not on the request loop: an archive that answers slowly holds up that one import, not every other page the hub is serving. Each importer gives up after its own timeout (30 seconds for ENA) and the control then reports the failure in place.

An identifier the archive resolves to no records of the kind the importer reads is reported as such, and the dataset is left untouched. This is not only a mistyped identifier: a record can exist and still hold nothing to import. The ENA importer reads sequencing runs, so a genome assembly project such as `PRJNA10719`, which ENA lists but which has no runs, imports as nothing.

An identifier the importer rejects as malformed, or an answer from the archive the importer cannot read, is reported as a failed import, not as an empty result: the two ask for different things of you, checking the identifier against the archive versus trying again later.

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
