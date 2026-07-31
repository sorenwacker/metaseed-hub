# Publishing and sharing

## Importing and exporting YAML

A specification can be exchanged as a YAML file.

- **Import** — on the Specs page or the **New Specification** screen, use **Import YAML** to create a draft from a file.
- **Export** — in the draft editor, click **Export** to download the specification as YAML, or **Preview** to view the YAML without downloading.

## Publishing a draft

When a draft is ready, click **Publish** in the editor. Publishing creates an immutable **published specification**. Published specifications appear under **Published Specifications** on the Specs page and can be selected as the profile for new datasets.

A published specification cannot be edited in place — this keeps datasets that reference it stable.

Publishing shares the specification with **every user of the hub**. A draft is the private form, visible only to you and anyone you shared it with; publishing is what makes a specification available to other people, who can then view it, fork it, and select it as the profile for their own datasets.

Publishing consumes the draft: the draft is removed and the published specification takes its place. Use **Unpublish** to withdraw it and get the draft back.

## The version bump gate

A profile version is `MAJOR.MINOR`. MAJOR means a dataset that validated under the previous version may fail under the new one; MINOR means every dataset valid under the previous version stays valid. Which changes are breaking, and why there is no patch component, is defined by metaseed — see [Profile versioning](https://sorenwacker.github.io/metaseed/api/schema-specs/#profile-versioning).

Publishing is the release event, so this is where the hub checks that the version you declared matches what actually changed. When a draft's profile name matches a specification already published in the same workspace, the hub compares the draft against the **latest published version** of that name and works out the bump the content requires:

- **A sufficient bump is published unchanged.** Declaring 2.0 for a breaking change, or 1.2 for a compatible one, publishes normally. Declaring a larger bump than required is allowed — a MAJOR bump for a purely compatible change is a judgement call, not an error.
- **An insufficient bump is refused.** Publishing stops, nothing is written, and the draft is left exactly as it was. The message lists the breaking changes it found (for example `Sample.tissue became required`) and names the version to declare instead.

A brand-new profile name has nothing to compare against, so it publishes at whatever version it declares. So does the first release after every earlier version of that name has been unpublished.

The gate only reads the version numbers and the content. It cannot tell whether the *meaning* of a field changed, so a rename that keeps the same shape still needs your judgement.

### What to do when publishing is refused

Change the draft's version to the one the message names — **Profile Settings** in the draft editor — and publish again. The alternative is to make the change non-breaking (leave the field optional, keep the removed field in place) and publish under the MINOR bump you originally declared.

## Versions the hub cannot read

`MAJOR.MINOR` has been the rule since metaseed 0.22, so a specification stored earlier may carry something else — `v1.0`, `1`, `1.0.0`, or `1.0-beta`. Opening one reports a fixable problem naming the stored value and the rule, instead of failing with a server error.

Existing rows were normalized in place when the hub upgraded: a leading `v` is stripped, a bare `1` becomes `1.0`, a pre-release or build suffix is dropped, and a third component is truncated (`1.2.3` becomes `1.2`). A value with no leading number — `draft`, `latest` — is never guessed at; it is left as it is and reported when opened, so you can decide what it should have been. Fix it in **Profile Settings** and save.

## Content hash

Every published specification records a content hash: a `sha256:` digest of its canonical content. A version number says how a specification relates to its predecessor; it does not identify one. Two files can both declare `cinema 1.1` and hold different content, and the hash is what tells them apart.

The first twelve digits appear on the specification's page. Datasets record the hash of the specification they were authored against, which is what lets the hub report when the specification has since changed underneath them (see [Datasets](../datasets/index.md)).

## Viewing and forking published specifications

On a published specification you can:

- **View** — open it read-only to inspect its entities, fields, and rules.
- **Fork** — create a new editable draft from it. Forking is how you make a new version of a published specification: edit the fork, then publish it as a new release. The published specification stays in place.

## Who a published specification belongs to

A published specification records two people, and they are not always the same:

- The **author** — whoever published it.
- The **workspace owner** — whose workspace it lives in.

They differ when a draft shared with you is published: the specification stays in the workspace it was shared from, and you are recorded as its author. Both names appear on the specification, each linking to an email address, so anyone can reach the author to report a problem or the owner about the workspace.

The Specs page lists every published specification, whoever published it. Forking one creates the copy in **your** workspace, not the original author's.

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
