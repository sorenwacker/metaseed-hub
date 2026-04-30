# Current Sprint: UI Improvements and Validation

## Status Summary

| Task | Status |
|------|--------|
| Fix entity save issue | Done |
| Field description positioning | Done |
| Add verify/validate button | Pending |
| Add comprehensive tests | In Progress |
| Update documentation | Pending |
| Graph view | Done |

## Completed Tasks

### 1. Entity Save Bug Fix
**Issue**: Saving entities showed "updated successfully" but old values returned.

**Root cause**: SQLAlchemy wasn't detecting changes to the JSONB `data` field.

**Fix**: Added `flag_modified(project, "data")` in `save_project_state()` to explicitly mark the JSONB field as modified.

```python
from sqlalchemy.orm.attributes import flag_modified

project.data = serialize_tree(state)
flag_modified(project, "data")
session.add(project)
await session.commit()
```

### 2. Field Description Positioning
**Issue**: Field descriptions appearing beside/behind inputs instead of below.

**Fix**: Added `display: block` to `.form-help` in hub.css:

```css
.form-help {
    display: block;
    font-size: 0.75rem;
    color: var(--clr-ink-light);
    margin-top: var(--space-xs);
    line-height: 1.4;
}
```

### 3. Graph View
- Full-page graph visualization with vis-network
- Accessible via "Graph View" button (opens in new tab)
- Per-entity graphing via "View in Graph" button on entity forms
- Legend with toggleable entity types
- Physics and hierarchical layout options

## Pending Tasks

### 4. Verify/Validate Button

**Goal**: Add a "Verify" button to validate project data against the metaseed schema.

**Implementation Plan**:

1. **Backend endpoint**: `POST /projects/{id}/validate`
   ```python
   @app.post("/projects/{project_id}/validate")
   async def validate_project(project_id: str, session: AsyncSession) -> Response:
       """Validate all entities in the project against schema."""
       state = get_project_state(project)
       facade = state.get_or_create_facade()

       errors = []
       for node in state.nodes_by_id.values():
           try:
               # Re-validate by recreating instance
               helper = getattr(facade, node.entity_type)
               data = node.instance.model_dump(exclude_none=True)
               helper.create(**data)  # Pydantic validation runs here
           except ValidationError as e:
               errors.append({
                   "node_id": node.id,
                   "entity_type": node.entity_type,
                   "label": node.label,
                   "errors": e.errors()
               })

       return JSONResponse({
           "valid": len(errors) == 0,
           "errors": errors,
           "entity_count": len(state.nodes_by_id)
       })
   ```

2. **UI button in project.html**:
   ```html
   <button class="btn"
           hx-post="/hub/projects/{{ project.id }}/validate"
           hx-target="#validation-results"
           hx-swap="innerHTML">
       Verify
   </button>
   <div id="validation-results"></div>
   ```

3. **Results template** - Show validation errors per entity with links to edit

### 5. Comprehensive Tests

**Files to add/update**:

| File | Content |
|------|---------|
| `tests/test_entity_persistence.py` | Serialize/deserialize, database persistence (Done) |
| `tests/test_validation.py` | Schema validation tests |
| `tests/test_graph_api.py` | Graph API endpoint tests |
| `tests/test_ui_forms.py` | Form submission tests |

**Validation tests to add**:
- Required field validation
- Type coercion (string to int, etc.)
- Constraint validation (min/max, pattern, enum)
- Nested entity validation
- Error message formatting

### 6. Documentation Updates

**ARCHITECTURE.md additions**:
- Graph view section
- Entity serialization/persistence flow
- Validation architecture

**New sections**:
```markdown
## Graph Visualization

The Hub includes a graph view for visualizing entity relationships.

### Access Points
- **Project Graph**: Click "Graph View" button in project header (opens new tab)
- **Entity Graph**: Click "View in Graph" on any entity form (shows entity + descendants)

### API Endpoint
`GET /projects/{id}/api/graph?node_id={optional}`

Returns JSON for vis-network:
```json
{
  "nodes": [{"id": "...", "label": "...", "group": "Investigation", ...}],
  "edges": [{"from": "...", "to": "..."}]
}
```

## Validation

Entity validation happens at multiple levels:

1. **Form submission**: Pydantic validation via `helper.create(**values)`
2. **Project validation**: `/projects/{id}/validate` endpoint validates all entities
3. **Export validation**: Full schema validation before export
```

## Next Steps

1. Implement validate endpoint
2. Add Verify button to UI
3. Add validation tests
4. Update ARCHITECTURE.md
5. Run full test suite
6. Commit changes

## Files Modified

- `src/metaseed_hub/ui/app.py` - Save fix, debug logging
- `src/metaseed_hub/ui/static/css/hub.css` - Form help styling
- `tests/test_entity_persistence.py` - New test file
