# Spec Builder

The Spec Builder is where you design custom metadata **specifications** — your own entity types, fields, relationships, and validation rules — instead of using a built-in profile. Open it from **Specs** in the header.

A specification exists in two states:

- **Draft** — editable; listed under **My Drafts**.
- **Published** — an immutable released version; listed under **Published Specifications**.

## Creating a specification

1. On the Specs page, click **+ New Specification**.
2. Choose how to start:
   - **From Scratch** — an empty specification.
   - **From Template** — start from a provided template.
   - **Import YAML** — upload an existing specification file (see [Publishing and sharing](publishing.md#importing-and-exporting-yaml)).
3. The draft editor opens.

## The draft editor

The editor combines an entity tree, a diagram (ERD) canvas, and a form editor, with **Profile**, **Rules**, **Comments**, and **Sharing** tabs in the sidebar.

### Entities

Click **+ Entity** on the canvas to add an entity, then click an entity to edit it. From the entity editor you can rename the entity and mark one entity as the specification's **root**.

### Fields

Add fields to an entity with **+ Field**. Each field has:

| Property | Purpose |
|----------|---------|
| Name | The field's identifier |
| Type | `string`, `integer`, `date`, `ontology_term`, `entity`, `list`, and others |
| Required | Whether a value is mandatory |
| Description | Help text |
| Ontologies | For ontology-constrained fields, the source ontologies |

### Relationships

Relationships are expressed through fields whose type references another entity:

- Use type **`entity`** for a one-to-one relationship (for example, *Study* has one principal investigator).
- Use type **`list`** for a one-to-many relationship (for example, *Investigation* has many *Persons*).

Set the field's **Related Entity** to the target entity. A `list` field also defines a parent–child hierarchy: the related entity appears as a child in the entity tree.

| Relationship | Field type | Related entity |
|--------------|-----------|----------------|
| Investigation has many Persons | `list` | Person |
| Study has one principal investigator | `entity` | Person |
| Person belongs to an Organization | `entity` | Organization |

## Saving

Click **Save** to persist draft changes.

## Next

- [Validation rules](validation-rules.md) — add custom constraints
- [Publishing and sharing](publishing.md) — import/export YAML, publish, and fork
