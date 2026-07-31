# Editing entities

Entities are the structural records inside a dataset — for example *Investigation*, *Study*, or *Sample*. The entity types available and how they nest are defined by the dataset's profile.

## The entity tree

The left sidebar shows the dataset's entities as a hierarchy. Click an entity to open its form in the center pane. Entity types that the profile allows but you have not added yet appear as **+ &lt;Entity&gt;** buttons in the sidebar.

## Adding an entity

1. Click **+ &lt;Entity&gt;** in the sidebar (for a root entity) or the add control on a parent entity (for a nested one).
2. Complete the form.
3. Click **Save**.

## Editing an entity

1. Select the entity in the tree.
2. The form groups **Required Fields** (marked with `*`) and **Optional Fields**, which are collapsed by default.
3. Edit the values and click **Save**. Each save records a new [version](versions.md).

Some fields are constrained to ontology terms. These provide a search box; see [Ontology lookup](../reference/ontology-lookup.md).

## Related entities and inline tables

When an entity contains a collection of nested entities, the form shows a **Related Entities** section with an inline table per relationship, labeled with the field name and item count.

- Click **+ Add Row** to add a nested entity.
- Edit cells inline, or open a row for the full form.
- Remove a row with its delete control.

## Validating

Click **Validate** to check the dataset against the profile schema. The result lists errors and warnings per entity and field so you can correct them. Validation does not change the data.

The result also names any stored entity that could not be loaded — for example one whose entity type the current specification no longer defines. Such an entity is not shown in the tree and is not part of the dataset you are editing, so saving removes it; the validation panel is where you find out before that happens.

## Deleting an entity

Use the delete control next to an entity in the tree or on an inline-table row. Deleting an entity records a new version, so the change is reversible by [restoring an earlier version](versions.md#restoring-a-version).
