# Versions and history

Metaseed Hub records a new version of a dataset every time you save a change, whether from the web interface, through the API or through an agent. Versions let you review what changed, compare two points in time, and revert.

Saves to one dataset are applied one after another, in the order the server receives them, also when two people, or a person and an agent, save at the same moment. Each save starts from what the previous one wrote, so simultaneous edits to different fields are both kept and each gets its own version. A save that arrives while another is in progress waits for it; in normal use the wait is not noticeable.

## The timeline

Open a dataset and select the **History** tab. It shows the dataset's timeline, newest first: the versions, grouped into editing sessions, and the events that happened to the dataset without changing its content.

If a dataset has no saved changes yet, the tab notes that versions are created automatically when you save.

### Editing sessions

Consecutive saves by the same person, each within 30 minutes of the previous one, form one **editing session**. A session is shown as one entry: who, when (first to last save), how many saves, and the summary of what changed between the version before the session and its last version. Expanding a session lists its versions `v12`, `v13`, … with their own summaries. A save by another person, or a gap of more than 30 minutes, starts a new session. Grouping is computed when the timeline is shown; it changes nothing that is stored.

### Named versions

Any version can be given a **name** and an optional note, for example *Submitted to ENA* with the accession in the note. Named versions are shown with their name at the top level of the timeline, also when they fall inside a session, and **Named only** filters the timeline to them. A name can be changed or removed later; the version itself is not affected. Naming, renaming and removing a name require the Editor or Owner role.

### Events

The timeline also records what happened to the dataset without changing its content. Each event names who caused it and when.

| Event | Recorded when |
|-------|---------------|
| **Created** | The dataset is created, with its origin: empty, from a file (its name), or from a repository (the accession) |
| **Shared** / **Access removed** | A person or a collaboration is given a role, has it changed, or loses it |
| **Exported** | The dataset is downloaded in a format (ISA-JSON, Excel, YAML, a repository format) or pushed to SEEK |
| **Restored** | A version is restored; the new version it creates names the version it restored |

Events are kept for as long as the dataset is. They are visible to everyone who can see the dataset.

### Which specification a version was saved under

Each version records the specification the dataset was bound to when it was saved: its name, version and content hash. The timeline shows it when it differs from the previous version's, so a change caused by a new specification can be told apart from an edit. Versions saved before this was recorded show no specification.

## Comparing versions

Click **Diff** on a version or a session to see the field-level changes it introduced — which entities and fields were added, removed, or modified relative to the previous version, or for a session, relative to the version before the session.

## Restoring a version

Click **Restore** on a version to return the dataset to that state. Restoring does not erase later history: it creates a new version whose contents match the chosen one, recorded with a **Restored** event that names the version, so the restore itself is reversible. The restored contents are saved the way any save is, checked against the dataset's current specification, so a version recorded under an older specification is reported by validation like any other data.

## Soft delete

Deleting a dataset is a **soft delete**. The dataset is marked deleted and excluded from your list, the editor, and all shared-access queries, but its rows are retained rather than physically removed. Related records such as comments, members and timeline events are left in place. Because names are freed on deletion, you can reuse the name of a deleted dataset for a new one.
