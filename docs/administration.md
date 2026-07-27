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

## Errors

The **Errors** section lists unhandled server errors, newest first, with the time, the request that failed, the exception type and message, and the signed-in user who hit it. A per-day count above the list makes a spike visible without reading every row.

These are recorded in the database rather than read from the host's journal, for two reasons: the application runs several worker processes that log separately, and the dashboard has no privilege to read the journal. A stored row also survives a restart.

Only the exception type and message are kept — never request bodies or headers — so the list does not accumulate a copy of users' data. Messages longer than 2000 characters are truncated. The user column is empty when the caller was not identified, and becomes empty if that account is later deleted; the error itself is kept.

Records are removed automatically 30 days after they occur. Recording never interferes with the error itself: the exception propagates unchanged, so the usual 500 response and the log line are exactly as before, and a failure to record is logged rather than raised.

## Security warnings

If the application is running with the default development `SECRET_KEY`, the dashboard shows a warning banner. Set a strong `SECRET_KEY` in the deployment environment to clear it; the secret is used to sign CSRF tokens.
