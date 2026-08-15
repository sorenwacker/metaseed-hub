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

### Filling several cells at once

A value that repeats down a column — the same protocol, unit, or growth facility for every row — can be entered once and applied to a block of cells.

1. Click the cell you want to start from.
2. Shift-click another cell in the same table. Every cell in the rectangle between the two is selected and highlighted.
3. Type the value in the cell you started from and press **Ctrl+Enter** (**Cmd+Enter** on macOS), or click **Apply to selection**.
4. Press **Escape** to clear a selection without applying anything.

The value is written to every selected cell, converted per column the same way a single cell edit is — a number column stores a number, a date column a date. The whole block is applied together and recorded as a single [version](versions.md), so undoing a bulk edit means restoring one earlier version rather than many.

Two kinds of cell are never written by a bulk apply, and are skipped even when they fall inside the selected rectangle:

- **Columns showing the parent reference** (greyed out, for example `investigation_id` on a Study table). This is the link between the row and the entity it belongs to; changing it would move rows to a different parent, which is done by editing the entity, not by filling cells.
- **Cells in a row that no longer exists** — if a collaborator deleted the row while your selection was open, the apply is refused as a whole and nothing is written, rather than partly applied. Reload the dataset and select again.

Selection spans one inline table. Cells in a different table, or in the entity form above it, are not part of it.

## Validating

Click **Validate** to check the dataset against the profile schema. The result lists errors and warnings per entity and field so you can correct them. Validation does not change the data.

The result also names any stored entity that could not be loaded — for example one whose entity type the current specification no longer defines. Such an entity is not shown in the tree and is not part of the dataset you are editing, so saving removes it; the validation panel is where you find out before that happens.

## Deleting an entity

Use the delete control next to an entity in the tree or on an inline-table row. Deleting an entity records a new version, so the change is reversible by [restoring an earlier version](versions.md#restoring-a-version).
