# Datasets

A **dataset** holds metadata entities organized according to a **profile** specification. Datasets are the main objects you work with in the hub; the **Datasets** page lists every dataset you own or that has been shared with you.

## The dataset list

Open **Datasets** in the header. **My Datasets** shows each dataset with its profile, version, and last-updated date. Click a dataset to open the editor.

## Creating a dataset

1. Click **+ New Dataset**.
2. On the **Standards** tab, select a **profile** and version. The newest version of each profile carries a *latest* badge. The supported profiles are listed in the [profiles reference](../reference/profiles.md).
3. Optionally enable example data to populate the dataset with a worked example.
4. Click **Create**.

To start from a file instead of an empty profile, use the **Import File** tab. See [Importing and exporting](import-export.md).

## The dataset editor

The editor has three areas:

| Area | Contents |
|------|----------|
| Left sidebar | The **entity tree** and **+ &lt;Entity&gt;** buttons for entity types not yet added |
| Center | The form for the entity currently selected |
| Right panel | **Overview**, **History**, **Comments**, and **Sharing** tabs |

From the sidebar you can also **Validate** the dataset, open the **Graph** view, **Import** into the dataset, **Export** it, and **Delete** it.

See:

- [Editing entities](entities.md) — entity tree, forms, inline tables, validation
- [Graph view](graph.md) — visualize entity structure
- [Versions and history](versions.md) — diff and restore
- [Importing and exporting](import-export.md)

## Deleting a dataset

Click **Delete** (the red button) in the dataset sidebar and confirm. A deleted dataset is removed from your list and excluded from all queries. Deletion is a soft delete: see [Versions and history](versions.md#soft-delete) for what this means.
