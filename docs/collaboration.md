# Collaboration

Datasets and specification drafts can be shared with other people and discussed through comments.

## Sharing and roles

Datasets, specification drafts and published specifications are shared the same way: open the item, click the **Sharing** tab in the sidebar, add a person by the email on their [profile](getting-started.md#your-profile), and choose a role.

| Role | Can do |
|------|--------|
| **Owner** | Everything: content, sharing, role changes, deletion |
| **Editor** | Change the content, not who has access |
| **Viewer** | Read |

### Who owns what you create

The person who creates a dataset, a draft, or a published specification is its first owner, recorded as a membership the moment it is created, on every path: the web interface, the REST API, the MCP tools, a hub push from metaseed, and an import. Ownership is a membership like any other, so it can be handed over. One rule decides who may do what: an explicit membership decides; failing that, the creator owns the item; failing that, the person whose account the item lives in owns it; failing that, a [collaboration grant](#collaborations) on the item gives everyone in that collaboration its role. An account belongs to one person, so the account rule is the item's home; the collaboration grant is the only group rule.

Datasets created before memberships were recorded at creation were given their account's user as owner when the rule arrived; where that user already held a lesser role on the dataset, the role was raised to owner rather than duplicated.

Every page of the web interface reads that rule, and so does the REST API when it is given a dataset's id. Two paths do not, and are scoped to your own account instead: the REST list of datasets together with every REST specification route, and the MCP tools. An item shared with you, or granted to your collaboration, is therefore reachable in the browser but not yet through a token or an agent.

The number on the **Sharing** tab is how many other people and collaborations have access: it leaves you out, so an item nobody else can reach shows no number. Only an owner can add someone, change a role, or remove access. Anyone can remove themselves, which is how you leave something shared with you.

### Handing something over

Make the other person an **Owner**, then remove yourself. Both of you can be owners in the meantime, and the item keeps working throughout.

An item always keeps at least one owner: the last one cannot be demoted, removed, or leave. Without that rule an item can end up with nobody able to share or delete it, recoverable only by an administrator.

Access is per item. Sharing one dataset grants nothing else, and sharing a published specification lets a colleague edit and re-publish it without giving them your datasets.

### How an email is resolved

Each person has their own account, and sharing reaches across accounts: the person you name does not have to be a colleague in any other sense.

- The address is matched without regard to capitalisation. `Ada@Example.org` and `ada@example.org` name the same account. Addresses are stored lowercased; the profile page still shows the address exactly as your identity provider reports it.
- One account exists per address, so a share never has to choose between candidates.
- The person must have signed in to the hub at least once. An account is created on first sign-in, and there is nothing to share with before that. If the hub says no account uses an address, ask them to sign in once and try again.

## Collaborations

The hosted hub authenticates through SURF Research Access Management (SRAM), where people are organised into *collaborations*, each with one or more *groups*. SRAM reports a person's groups at sign-in as `eduperson_entitlement` URNs of the form `urn:mace:surf.nl:sram:group:<organisation>:<collaboration>:<group>`. The hub uses them for two things: finding the people you work with, and sharing an item with a whole collaboration at once. A local instance authenticates through Keycloak, whose development realm emits the same URNs, so everything here works locally.

### Your collaborations

The hub does not keep its own list of who is in which collaboration. It takes a reading: the group URNs your identity provider reported, and when it read them. A sign-in takes one and replaces whatever was there, so leaving every collaboration takes effect at your next sign-in. Opening any page also takes one when there is none or it has gone stale, which is what saves a session older than this feature from never having one. Your [profile](getting-started.md#your-profile) lists the result under **Your collaborations** with the time it was read.

The snapshot exists because two things cannot be answered from the sign-in token alone. Listing the people in a collaboration needs everyone's membership, not just yours. And a request made with an access token, which is how metaseed and MCP clients call the hub, carries no entitlements at all, so without the snapshot a dataset shared with your collaboration would be invisible to those clients.

A reading older than the configured limit (`MEMBERSHIP_MAX_AGE_DAYS`, 30 by default) is not trusted: it no longer grants access and no longer lists you among a collaboration's people. This bounds how long someone who has left a collaboration keeps reaching its items through an access token.

Seeing no collaboration means one of three things, and the page says which:

| What you see | What it means |
|---|---|
| The hub has not read your collaborations | No reading has been taken. Sign out and in again. |
| The reading is too old to trust | It grants nothing until it is taken again. |
| Your identity provider reported none | A reading was taken and named no group. |

An access token carries no group membership at all, which is why the reading exists; a request made with one therefore never takes a reading, and never clears the one you have.

### People in your collaborations

**People** in the header lists every collaboration you are in and, for each, the members who have signed in to the hub at least once, with the name and address on their profile and when they last signed in. You see only the collaborations you belong to. Someone who has never signed in is not listed, because the hub has no record of them; SRAM itself remains the authoritative list.

The email field on the **Sharing** tab suggests these people as you type, so sharing with a colleague no longer means asking them for the address on their profile.

### Publishing to a collaboration

A specification can be published so that only a collaboration's members see it, which is the middle ground between a private draft and a release to the whole hub. See [Who can see it](spec-builder/publishing.md#who-can-see-it).

### Sharing with a collaboration

On the **Sharing** tab, under **Collaborations**, an owner can grant a whole collaboration a role. The choice lists the collaborations the owner is in. A grant gives every member of the collaboration the role **Editor** or **Viewer**; **Owner** cannot be granted to a collaboration, because ownership is what lets a person share, hand over and delete, and an item whose owners are "whoever is in a group this month" cannot keep the [last-owner rule](#handing-something-over). An owner can change a grant's role or remove it.

A grant is a fallback. A person's explicit membership on the item always wins, so someone in the collaboration can still be given a different role individually. The item's creator and the account it lives in stay owners regardless of any grant.

Items reachable through a grant appear in your dataset and specification lists like items shared with you individually, with the collaboration named. They appear for a person only while their membership snapshot is fresh, as described above.

## Comments

The **Comments** tab provides threaded discussion on a dataset.

- Write a comment and click **Post**.
- **Reply** to a comment to start a thread. Replies nest up to two levels deep.
- React to a comment with **Like** or **Dislike**.
- Delete a comment you authored.

Specification drafts have their own **Comments** tab that works the same way.

## Presence

Who is in a dataset room is kept in Redis, not per process: each instance
writes its own connections into a per-room sorted set scored by a heartbeat
timestamp, and reads the whole set back when presence is rendered. An
instance that dies stops refreshing its entries, and they age out after
three missed heartbeats — so a crashed process's users disappear from
presence without any cleanup handshake. Without Redis (single-process
development), presence falls back to the process's own connection list,
which is then also the whole truth.
