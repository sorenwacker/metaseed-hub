# Getting Started

This page takes you from logging in to a first saved dataset.

## Logging in

Metaseed Hub uses OpenID Connect (OIDC) for authentication. The hosted instance authenticates through SURF SRAM; a local instance authenticates through Keycloak.

1. Open the hub. On the hosted instance this is [https://metaseed.ewi.tudelft.nl](https://metaseed.ewi.tudelft.nl); a local instance runs at `http://localhost:7001/hub/`.
2. Click **Login** in the top-right header.
3. Complete sign-in with your identity provider. You are returned to the dataset list.

Your name and initials appear in the header. Click the avatar to open [Your Profile](#your-profile); click **Logout** to end the session.

### When your session expires

A sign-in lasts as long as the identity provider keeps the session alive; after that the hub cannot act for you any more. The next thing you do — opening a page, following a link, editing a cell — takes you to the sign-in page instead. Signing in returns you to the page you were trying to reach, not to the dataset list.

Pages are never served from the browser's cache, so going back, or returning to a tab you left open, asks the hub again rather than redrawing what was on screen before. An expired session therefore shows the sign-in page, not a dataset list you can no longer open.

### Your profile

The profile page (avatar → **Your Profile**) shows the account information read from your OIDC token: name, email, subject ID, and any roles. The email shown under **Sharing** is the address collaborators use to share datasets with you, and they may type it in any capitalisation. See [how an email is resolved](collaboration.md#how-an-email-is-resolved).

## Create your first dataset

1. From **Datasets**, click **+ New Dataset**.
2. Choose a **profile** and version on the **Standards** tab — for example MIAPPE, ISA, DiSSCo, or Darwin Core. The newest version of each profile is marked *latest*.
3. Optionally enable example data to start from a populated dataset.
4. Click **Create**. The dataset editor opens.

Alternatively, switch to the **Import File** tab to create a dataset from an existing ISA-JSON, YAML, or Excel file. See [Importing and exporting](datasets/import-export.md).

## Add and save entities

1. In the editor, the left sidebar lists the entity types the profile allows. Click **+ &lt;Entity&gt;** (for example **+ Investigation**) to add one.
2. Fill in the required fields, marked with `*`.
3. Click **Save**. Saving records a new [version](datasets/versions.md) automatically.

Continue adding entities, then click **Validate** to check the dataset against the profile schema. See [Editing entities](datasets/entities.md) for the full editing workflow.

## Next steps

- [Create and manage datasets](datasets/index.md)
- [Share a dataset with collaborators](collaboration.md)
- [Design a custom specification](spec-builder/index.md)

!!! note "Running a local instance"
    Installing and running the hub on your own machine (`make dev`, containers, migrations) is covered in the [Developer](developer/architecture.md) section, not in this manual.
