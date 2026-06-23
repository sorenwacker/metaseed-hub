# Validation rules

Validation rules add constraints to a specification beyond field types and the required flag. They are checked when a dataset built on the specification is validated.

## Managing rules

In the draft editor, open the **Rules** tab in the sidebar. The tab shows the rules defined for the specification, with a count badge. Add a rule with **+ Rule**, then set:

- a **name** for the rule, and
- the entity or field it **applies to**, with the rule's condition.

Rules can express constraints such as a required field, a unique value, a pattern a value must match, or a dependency between fields or entities.

Remove a rule from the same tab. Rules are part of the draft and are saved with it.

## How rules apply

When someone runs **Validate** on a dataset that uses the specification, the dataset is checked against the field definitions and these rules together. See [Editing entities — validating](../datasets/entities.md#validating).
