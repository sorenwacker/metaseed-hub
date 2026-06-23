# Ontology lookup

Some profile fields are constrained to terms from a controlled vocabulary (an ontology). These fields provide an integrated search so you can pick a recognized term instead of typing free text.

## Using the lookup

1. In an entity form, an ontology-constrained field shows a search box with the source ontologies indicated.
2. Start typing. Matching terms are suggested as you type.
3. Select a term to store it on the field.

Choosing terms from the ontology keeps datasets consistent and interoperable, and lets validation confirm that constrained fields hold recognized terms.

!!! note
    Which ontologies a field accepts is set by the profile or by the field definition in the [Spec Builder](../spec-builder/index.md#fields). Lookups query the configured ontology service.
