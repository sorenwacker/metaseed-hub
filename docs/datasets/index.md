# Datasets

A **dataset** holds metadata entities organized according to a **profile** specification. Datasets are the main objects you work with in the hub; the **Datasets** page lists every dataset you own or that has been shared with you.

## The dataset list

Open **Datasets** in the header. The list holds every dataset you own and every one shared with you, in two views you switch between with **Cards** and **Table** above the list; the hub remembers which you used last.

**Cards** show each dataset with its profile, version, last-updated date, and how many entities it holds; hovering over the count shows the number per entity type. A dataset shared with you through a collaboration carries the collaboration's name.

**Table** shows the same datasets one per row, with the columns **Name**, **Profile**, **Version**, **Entities**, **Updated** and **Access**, and is where you find one among many:

- **Search** matches the name, the description and the profile as you type; the list narrows without reloading the page.
- **Profile** narrows the list to one profile; the choice lists the profiles your datasets use.
- **Access** narrows the list to datasets you own, datasets shared with you by a person, or datasets reached through a collaboration.
- Click a column heading to sort by it; click again to reverse. The list arrives sorted by last update, newest first.

The filters live in the page address, so a filtered table can be bookmarked or sent to a colleague, who sees it narrowed the same way over their own datasets. Click a row to open the editor.

## Creating a dataset

1. Click **+ New Dataset**.
2. On the **Standards** tab, select a **profile** and version. The newest version of each profile carries a *latest* badge. The supported profiles are listed in the [profiles reference](../reference/profiles.md).
3. Optionally enable example data to populate the dataset with a worked example.
4. Click **Create**.

A dataset name is unique within your account, and a deleted dataset keeps its name. A name you already use, or one held by a dataset you deleted, is refused and nothing is created; the form stays open and says why, so you can choose another name. The same applies when you import a file or a repository record. When creating a dataset fails for another reason, such as an accession that resolves to nothing, the form says that instead.

To start from a file instead of an empty profile, use the **Import File** tab. See [Importing and exporting](import-export.md).

## The dataset editor

The editor has three areas:

| Area | Contents |
|------|----------|
| Left sidebar | **+ &lt;Entity&gt;** buttons for entity types not yet added, the dataset actions, and two tabs: **Entities** (the entity tree) and **Sharing** |
| Center | The [entity overview](entities.md#the-entity-overview) with **History** and **Comments** tabs when no entity is selected; otherwise the form for the selected entity |

From the sidebar you can also **Validate** the dataset, open the **Graph** view, **Import** into the dataset, **Export** it, and **Delete** it.

## Specification drift

Every save records the content hash of the specification the dataset was authored against. When you validate, the hub compares that stamp with the specification's current hash and reports **specification drift** if they differ — the specification changed after this dataset was written, so entities that were complete when you saved them may no longer be.

Drift is reported, not enforced: the dataset still opens, still edits, and still validates against the current specification. The report tells you why a dataset you had finished suddenly has issues.

Datasets saved before the hub started recording the stamp have none. Their provenance is unknown rather than unchanged, so no drift is reported for them; the stamp is recorded the next time the dataset is saved.

See:

- [Editing entities](entities.md) — entity tree, forms, inline tables, validation
- [Graph view](graph.md) — visualize entity structure
- [Versions and history](versions.md) — diff and restore
- [Importing and exporting](import-export.md)

## Deleting a dataset

Click **Delete** (the red button) in the dataset sidebar and confirm. A deleted dataset is removed from your list and excluded from all queries. Deletion is a soft delete: see [Versions and history](versions.md#soft-delete) for what this means.

## FAIR exposure

A dataset page is also its catalog record. The page embeds the dataset's
DCAT description as JSON-LD, the dataset URL answers content negotiation
(`Accept: application/ld+json` or `text/turtle` returns the record itself),
and every response carries a `Link: rel="describedby"` header — the three
signals FAIR harvesters such as F-UJI read. Reachability follows the
deployment's authentication: harvesting needs whatever visibility the
operator grants the dataset URL.

An opt-in FAIRness regression check runs F-UJI against a reachable dataset
URL (`tests/test_fuji_fairness.py`; set `FUJI_URL` and `FUJI_TARGET`). It
asserts the FsF score does not regress below a baseline, which is how a
change that quietly removes a harvestability signal gets noticed.
