# Spec Builder

The Spec Builder is where you design custom metadata **specifications** — your own entity types, fields, relationships, and validation rules — instead of using a built-in profile. Open it from **Builder** in the header.

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

The editor combines an entity tree, a diagram (ERD) canvas, and a form editor, with **Profile**, **Rules**, **Checks**, **Comments**, and **Sharing** tabs in the sidebar.

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

Under **Markers** the field editor carries the declarative annotations a consumer of the specification reads: the identifier and label markers, the completeness tier, a human-readable label, a unit, an example value, allowed values, the ownership marker for relationship fields, and the DCAT property described below.

#### DCAT property

A dataset can also be described as a [DCAT](https://www.w3.org/TR/vocab-dcat-3/) catalogue record — the discovery-level statement that the dataset exists and where to get it, rather than what is inside it. The record is not written by hand: each field's **DCAT property** marker names the property of the catalogue record that the field's value supplies. Setting a field's DCAT property to `dct:title`, for example, makes that field's value the record's title.

The marker is only read on the specification's **root entity**, because a catalogue record describes the dataset as a whole. The field's own value is what ends up in the record, so the marker belongs on a field that carries dataset-level information (a submission date, a licence, a contact list).

The input suggests the properties metaseed resolves:

| Property | Fills |
|----------|-------|
| `dct:title` | The record's title |
| `dct:description` | The record's description |
| `dct:identifier` | The record's identifier |
| `dct:issued` | The publication date |
| `dct:license` | The licence |
| `dct:accessRights` | The access rights statement |
| `dct:publisher` | The publishing organisation |
| `dct:relation` | Related resources |
| `dct:source` | Resources this dataset was derived from, such as a repository accession |
| `dct:conformsTo` | Standards the dataset conforms to |
| `dcat:contactPoint` | The contact, taken from a contacts field |
| `dcat:landingPage` | The dataset's landing page |
| `dcat:keyword` | Keywords |
| `dcat:theme` | Themes |

The field accepts any value, since DCAT-AP profiles define further properties, but a property outside this list is recorded in the specification without affecting the catalogue record.

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

## Checks

The **Checks** tab in the sidebar reports what the draft looks like to metaseed. It separates two kinds of finding, because they call for different responses:

- **Problems** are defects. The specification does not build, so a dataset cannot be created from it until they are fixed — an undefined root entity, a field referring to an entity that is not there, a profile version that is not `MAJOR.MINOR`.
- **Advisories** are advice. The specification builds and works; something in it is probably not what the author intended. An advisory never makes the draft invalid.

The advisory reported today concerns the **identifier**. Every entity has one: the field whose value identifies a record, used wherever a record has to be named or linked. If no field is marked as the identifier, the first field that is not a reference is used. That inference is silent, so it is reported when the field it lands on is a weak choice — an optional free-text field with no pattern, allowed values, or uniqueness scope, whose name does not say it is an identifier. Such a field can be empty or repeated across records, so records become indistinguishable. Mark the intended field with the **Identifier** marker to settle it.

Neither kind blocks saving or publishing. The tab is a report, not a gate.

## Saving

Click **Save** to persist draft changes. Each edit to an entity, field, or rule is persisted as you make it; **Save** records the draft as a whole.

A save rewrites the draft's entire specification, so it is refused when the draft has changed elsewhere since you opened it — in another tab, or by a collaborator who has access to it. You get "This spec changed somewhere else since you opened it" rather than a success, because applying your copy would silently discard the other change. Reload the page to pick up the current version and reapply your edit.

## Next

- [Validation rules](validation-rules.md) — add custom constraints
- [Publishing and sharing](publishing.md) — import/export YAML, publish, and fork
