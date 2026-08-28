# Collaboration

Datasets and specification drafts can be shared with other people and discussed through comments.

## Sharing and roles

Datasets, specification drafts and published specifications are shared the same way: open the item, find **Sharing**, add a person by the email on their [profile](getting-started.md#your-profile), and choose a role.

| Role | Can do |
|------|--------|
| **Owner** | Everything: content, sharing, role changes, deletion |
| **Editor** | Change the content, not who has access |
| **Viewer** | Read |

### Who owns what you create

The person who creates a dataset, a draft, or a published specification is its first owner, recorded as a membership the moment it is created, on every path: the web interface, the REST API, the MCP tools, a hub push from metaseed, and an import. Ownership is a membership like any other, so it can be handed over and it never depends on which account the item lives in. Datasets created before this rule existed were given their account's user as owner when the rule arrived.

Only an owner can add someone, change a role, or remove access. Anyone can remove themselves, which is how you leave something shared with you.

### Handing something over

Make the other person an **Owner**, then remove yourself. Both of you can be owners in the meantime, and the item keeps working throughout.

An item always keeps at least one owner: the last one cannot be demoted, removed, or leave. Without that rule an item can end up with nobody able to share or delete it, recoverable only by an administrator.

Access is per item. Sharing one dataset grants nothing else, and sharing a published specification lets a colleague edit and re-publish it without giving them your datasets.

### How an email is resolved

Each person has their own account, and sharing reaches across accounts: the person you name does not have to be a colleague in any other sense.

- The address is matched without regard to capitalisation. `Ada@Example.org` and `ada@example.org` name the same account. Addresses are stored lowercased; the profile page still shows the address exactly as your identity provider reports it.
- One account exists per address, so a share never has to choose between candidates.
- The person must have signed in to the hub at least once. An account is created on first sign-in, and there is nothing to share with before that. If the hub says no account uses an address, ask them to sign in once and try again.

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
