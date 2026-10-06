# Ontology lookup

Some profile fields are constrained to terms from a controlled vocabulary (an ontology). These fields provide an integrated search so you can pick a recognized term instead of typing free text.

## Using the lookup

1. In an entity form, an ontology-constrained field shows the ontologies it takes and a **Search** button.
2. Click **Search**, or press **Tab** while the cursor is in the field. A search window opens for the field's ontologies.
3. Type a name or part of one. Each result shows the term's identifier, its name and, where the ontology gives one, the start of its definition.
4. Click a result to select it, then **Done**. The field stores the term's identifier, such as `NCBITaxon:3702`.

Below the field the hub shows what the stored identifier means: the term's name, its synonyms and its definition, as far as the ontology holds them. A taxon from NCBITaxon has a scientific name and common names but no definition. An identifier typed or pasted by hand is looked up the same way when you leave the field, so a mistyped one is visible before you save: the hub says that it found no such term. A value that is not an identifier is left without a note; [validation](../datasets/entities.md) reports it as not checked.

In the [Spec Builder](../spec-builder/index.md), the ontology term of an entity is suggested as you type instead.

Choosing terms from the ontology keeps datasets consistent and interoperable, and lets validation confirm that constrained fields hold recognized terms.

!!! note
    Which ontologies a field accepts is set by the profile or by the field definition in the [Spec Builder](../spec-builder/index.md#fields). Lookups query the configured ontology service.
