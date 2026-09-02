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
