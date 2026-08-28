# Container updates

Four services on the deployment host run as Docker containers: PostgreSQL, Redis, MariaDB, and Matomo. The hub application itself does not — it runs under systemd from a checkout of a release tag, and `deploy.sh` updates it. The two paths are separate, and this page covers the container one.

## Why images go stale on their own

A container image is a fixed set of packages. The upstream projects keep releasing: PostgreSQL ships minor releases roughly quarterly, each naming the security fixes it contains, and the Debian base underneath the image is patched more often than that. Nothing on the host changes, but the number of published advisories that apply to a running image grows every month. A vulnerability scanner reports this as a rising count against a deployment that has not been touched.

Docker does not close this gap by itself. A tag such as `postgres:16` is resolved once, at pull time; afterwards the host keeps that image indefinitely. Rebooting does not re-resolve it, and neither does `docker compose up -d`, which starts the existing container from the existing local image. Without an explicit pull, an image stays at the version it had on the day it was first fetched.

## Why the tags name an exact minor

Every image in `ansible/roles/metaseed-hub/files/docker-compose.yml` is pinned to a full version — `postgres:16.15`, not `postgres:16`. Two reasons.

The first is that a floating tag hides the upgrade. With `postgres:16` the running version depends on when the host last pulled, which is not recorded anywhere and differs between hosts. A full pin makes the deployed version a fact in the repository, visible in the diff and in `git log`.

The second reason applies to the databases and is the more serious one. PostgreSQL and MariaDB cannot open a data directory written by a newer major version. If a tag were allowed to float across a major boundary, the first pull that crossed it would leave the container exiting at startup with an incompatible-data-directory error and the hub down until someone ran `pg_upgrade` or restored from a dump. Major upgrades need a planned data migration, so they must never arrive as a side effect of a pull.

## How updates arrive

Renovate (`renovate.json`, on the `config:best-practices` preset) watches the compose file and opens a pull request when a pinned image has a newer version, pinning it to its digest. Major bumps are disabled for images, so PostgreSQL 16 stays on 16 and MariaDB 11 stays on 11; crossing a major is a deliberate, separately planned piece of work. Minor and patch bumps are grouped into a single weekly pull request, which Renovate merges itself once CI passes (`automerge` for minor, patch, pin and digest updates).

The result is that keeping the images current is not per-container manual work. Merging happens without intervention; the version change lands in `main` as a reviewable commit either way.

## How updates reach the host

`metaseed-containers.timer` runs weekly on the deployment host. It fetches `origin/main`, reads the compose file from there, and if the pins differ from what is deployed it pulls the new images and recreates the containers whose image changed. Containers whose pins did not move are left running untouched.

This follows `main` rather than a release tag, unlike the application. Application deploys are tag-driven because the version is derived from the tag by setuptools-scm and a dev build must never reach production; an image pin has no such property. Tying container updates to tags would mean an auto-merged security bump waits in `main` until someone decides to cut an unrelated application release, which is the gap this timer exists to close.

The application path is untouched by this: `deploy.sh` handles git checkout, `uv sync`, migrations, and the service restart, and never invokes Docker.

## Applied on every release too

A release deploy (`deploy.sh`, on a new tag) finishes by running the container update script, so a pin merged with a release lands with it rather than waiting for the next Monday. The weekly timer still covers pins merged without a release. The update runs after the release is healthy and its failure is logged but does not fail the deploy: the application is on the new version either way, and the pins get another chance on the timer.

## Guards on the unattended update

Recreating the PostgreSQL container restarts the database. Connections drop and the hub returns errors for the few seconds the container takes to come back. Because this happens without anyone watching, the update script refuses to proceed unless both conditions hold:

A backup from the last 24 hours exists in `/var/backups/metaseed-hub`. This guards against the container failing to come back for a reason unrelated to the version change; a minor upgrade does not migrate the data directory, so the newer server reads the existing files as they are.

The hub answers its health endpoint after the recreate, checked the same way `deploy.sh` checks it. A failed health check is logged as an error to the journal, so a broken update cannot pass for a quiet week.

The timer fires at 04:15 UTC on Mondays with `Persistent=false`, so it runs in a low-traffic window and does not fire on boot. A missed week is picked up the following Monday.

## Applying an update by hand

```bash
sudo -u app /opt/metaseed-docker/update-containers.sh
```

The script is the same one the timer runs. To apply a pin change immediately after merging it, run this rather than waiting for Monday.

Confirm what came up:

```bash
docker exec metaseed-postgres postgres --version
docker exec metaseed-redis redis-server --version
```

## Checking for drift

`metaseed_hub.container_drift` compares the images the host is running against the pins in the compose file:

```bash
sudo -u app /app/.venv/bin/python -m metaseed_hub.container_drift
```

It exits non-zero and names every container whose running image does not match its pin. The administration dashboard shows the same comparison, so drift is visible without shell access. Drift means the timer has not applied a pin change — either it has not fired since the merge, or it failed; `journalctl -u metaseed-containers.service` says which.

## Compose version requirement

All of this needs Docker Compose v2 (`docker compose`). Compose 1.29.2 cannot recreate containers against the installed Docker Engine — it fails with `KeyError: 'ContainerConfig'` after having already stopped the old container, which takes the service down and leaves it down. The role installs the `docker-compose-v2` package for this reason, and the update script refuses to run if only v1 is present.

## Where Renovate runs

Renovate runs from this repository's own workflow, `.github/workflows/renovate.yml`: weekly (early Monday, UTC) and on demand from the Actions tab (**Renovate > Run workflow**), reading `renovate.json`. The hosted Mend app is installed but has never run on this repository; a workflow here has a visible log and needs nothing enabled elsewhere.

The workflow needs one secret, `RENOVATE_TOKEN`: a fine-grained personal access token for this repository with read and write access to *Contents*, *Pull requests*, *Issues*, and *Workflows*. It can't use the workflow's own `GITHUB_TOKEN`, because pull requests opened with that token trigger no other workflows — the CI gate would never run on an update and nothing could auto-merge. If the hosted app starts running as well, disable one of the two, or every update arrives twice.
