# Administration

The admin dashboard provides aggregated usage statistics. It is available only to users whose OIDC token carries the configured admin role; it does not appear in the navigation for other users.

Open it at `/hub/admin/`.

## What the dashboard shows

| Section | Contents |
|---------|----------|
| **System Overview** | Total active users and total datasets |
| **Activity (Last 30 Days)** | Daily counts of user registrations and dataset creations |
| **User Directory** | Registered users with email, display name, registration date, dataset count, and last sign-in |

The dashboard reports aggregated counts and directory information only; it does not expose the contents of users' datasets.

### Datasets per user

The **Datasets** column counts the datasets in that user's own workspace, excluding deleted ones. Every user gets a workspace of their own on first sign-in, and that workspace is what owns a dataset, so this is the count of datasets the user created rather than of datasets they can see. A dataset shared with them belongs to the workspace of whoever created it and is counted there.

### Last sign-in

The **Last sign-in** column records when the user last completed the sign-in flow, not their last request. It is written once per sign-in, so an active session that has run for days still shows the sign-in that started it, and a user who stays signed in shows an older date than their real activity.

Users who registered before this column existed show `Never` until their next sign-in. That is a gap in the record, not evidence that they have not used the hub.

## Security warnings

If the application is running with the default development `SECRET_KEY`, the dashboard shows a warning banner. Set a strong `SECRET_KEY` in the deployment environment to clear it; the secret is used to sign CSRF tokens.
