# Publishing and sharing

## Importing and exporting YAML

A specification can be exchanged as a YAML file.

- **Import** — on the Specs page or the **New Specification** screen, use **Import YAML** to create a draft from a file.
- **Export** — in the draft editor, click **Export** to download the specification as YAML, or **Preview** to view the YAML without downloading.

## Publishing a draft

When a draft is ready, click **Publish** in the editor. Publishing creates an immutable **published specification**. Published specifications appear under **Published Specifications** on the Specs page and can be selected as the profile for new datasets.

A published specification cannot be edited in place — this keeps datasets that reference it stable.

Publishing consumes the draft: the draft is removed and the published specification takes its place. Until now that was one-way — there was no way to get the draft back.

## Viewing and forking published specifications

On a published specification you can:

- **View** — open it read-only to inspect its entities, fields, and rules.
- **Fork** — create a new editable draft from it. Forking is how you make a new version of a published specification: edit the fork, then publish it as a new release. The published specification stays in place.

## Who a published specification belongs to

A published specification records two people, and they are not always the same:

- The **author** — whoever published it.
- The **workspace owner** — whose workspace it lives in.

They differ when a draft shared with you is published: the specification stays in the workspace it was shared from, and you are recorded as its author. Both names appear on the specification, each linking to an email address, so anyone can reach the author to report a problem or the owner about the workspace.

Your Specs page lists published specifications in your own workspace **and** any you authored elsewhere. Without that, publishing a shared draft made the specification vanish from the author's view entirely, with nothing to indicate where it had gone.

## Unpublishing

**Unpublish** withdraws a published specification from the tenant and returns it to you as a private draft. Use it when something was published that should have stayed private, or when a release was made by mistake.

Unpublishing:

- Removes the specification from **Published Specifications**, from the profile choices offered when creating a dataset, and from the Explorer.
- Removes it for anyone you shared it with.
- Creates a new draft owned by you, carrying the full specification, so no work is lost. You are taken straight to it.
- Is available to the person who published the specification and to tenant admins and owners — the same people who may edit it.

Unpublishing does not delete anything permanently: the specification is withdrawn rather than erased, so an administrator can still account for what existed and when it was withdrawn.

Datasets already created against the specification keep their own copy of it and continue to open and validate normally. What changes is that the specification can no longer be chosen for *new* datasets.

To publish again after fixing whatever was wrong, publish the draft as usual.

## Sharing a draft

Drafts have a **Sharing** tab with the same Owner / Curator / Viewer roles as datasets, so collaborators can work on a specification together. See [Collaboration](../collaboration.md#sharing-and-roles).
