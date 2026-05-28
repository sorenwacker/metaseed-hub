# Adding Relationships in Specs

In the spec builder, relationships are created through fields with type `list` or `entity`:

1. Go to the entity that should have the relationship (e.g., `Investigation`)
2. Add a new field with:
   - Name: e.g., `contact_persons`
   - Type: `list` (for one-to-many) or `entity` (for one-to-one)
3. Click the field to edit it, then set:
   - Related Entity: `Person`

This creates a relationship like:
```
Investigation → contact_persons → [Person]
```

## Example Relationship Types

| Relationship | Field Type | Related Entity |
|--------------|------------|----------------|
| Investigation has many Persons | `list` | Person |
| Study has one Principal Investigator | `entity` | Person |
| Person belongs to Organization | `entity` | Organization |

## Parent-Child Hierarchies

For parent-child hierarchies (e.g., Study under Investigation):
- Add a `list` field on Investigation called `studies` with Related Entity: `Study`
- The spec builder will automatically show Study as a child in the entity tree
