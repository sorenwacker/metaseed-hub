# Supported profiles

A **profile** is the metadata standard a dataset follows. It defines the available entity types, their fields, the root entity, and how entities nest. Metaseed Hub ships the profiles below and also accepts custom specifications built in the [Spec Builder](../spec-builder/index.md).

| Profile | Scope |
|---------|-------|
| **MIAPPE** | Minimum Information About a Plant Phenotyping Experiment |
| **ISA** | Investigation–Study–Assay model for life-science experiments |
| **DiSSCo** | Digital specimen standard for natural-history collections |
| **Darwin Core** | Biodiversity occurrence data |

Each profile is versioned. When you [create a dataset](../datasets/index.md#creating-a-dataset), you select both a profile and a version; the newest version is marked *latest*. Use the [Explorer](../explorer.md) to inspect a profile's entities and fields or to compare versions and profiles.

!!! note
    The exact entity types and fields are defined by the selected profile and version. The hub generates forms and validation directly from that definition, so the dataset editor always reflects the chosen profile.
