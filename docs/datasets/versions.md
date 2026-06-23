# Versions and history

Metaseed Hub records a new version of a dataset every time you save a change. Versions let you review what changed, compare two points in time, and revert.

## Viewing history

Open a dataset and select the **History** tab in the right panel. Each version is listed as `v1`, `v2`, and so on, with its timestamp, author, and a short summary of the change (for example, entities added or removed).

If a dataset has no saved changes yet, the tab notes that versions are created automatically when you save.

## Comparing versions

Click **Diff** on a version to see the field-level changes it introduced — which entities and fields were added, removed, or modified relative to the previous version.

## Restoring a version

Click **Restore** on a version to return the dataset to that state. Restoring does not erase later history: it creates a new version whose contents match the chosen one, so the restore itself is reversible.

## Soft delete

Deleting a dataset is a **soft delete**. The dataset is marked deleted and excluded from your list, the editor, and all shared-access queries, but its rows are retained rather than physically removed. Related records such as comments and members are left in place. Because names are freed on deletion, you can reuse the name of a deleted dataset for a new one.
