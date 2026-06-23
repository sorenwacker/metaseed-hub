# Explorer

The Explorer lets you browse the structure of a metadata profile and compare two profiles side by side. Open it from **Explorer** in the header.

## Browsing a profile

1. Select a profile under **Base Profile (Reference)**.
2. Click **Explore**.

The profile's entities and fields are drawn as an entity–relationship diagram. Use the canvas controls to zoom, pan, **Fit** the diagram to the view, and switch between automatic (physics) and hierarchical **Layout**. Zoom in on an entity to read its fields.

## Comparing two profiles

1. Select a **Base Profile (Reference)**.
2. Select a second profile under **Compare Against**.
3. Click **Compare**.

The diagram overlays both profiles and highlights the differences:

| Legend | Meaning |
|--------|---------|
| **Common** | Present and unchanged in both profiles |
| **Added** | Present only in the compared profile |
| **Removed** | Present only in the base profile |
| **Modified** | Present in both but with field-level differences |

### Entity details

Click an entity to open the **Entity Details** panel. It shows the entity's status, which profiles it is **Present In**, and the field-level differences (added, removed, or modified fields).

### Filtering

In compare mode, the **Show/Hide** controls let you toggle which entities are shown — common, base-only, or compare-only — so you can focus on the differences. **Show All** restores everything.
