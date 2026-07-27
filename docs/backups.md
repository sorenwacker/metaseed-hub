# Backups and restore

The hub's PostgreSQL database holds every dataset, spec draft, comment, and version history. A systemd timer on the deployment host dumps it daily and prunes old dumps under a tiered retention policy.

## What is backed up

`pg_dump --format=custom` of the whole `metaseed_hub` database, taken from inside the `metaseed-postgres` container. The custom format is compressed and restores selectively with `pg_restore`.

Dumps are written to `/var/backups/metaseed-hub` as `metaseed_hub-YYMMDD-HHMMSS.dump`. The timestamp in the name is UTC and is what the retention policy reads; nothing depends on file mtime, so copying dumps between hosts does not change how long they are kept.

Redis is not backed up: it holds only ephemeral session and websocket state, which is rebuilt on reconnect.

## Retention

Keeping every daily dump forever costs storage; keeping only the last few loses the ability to recover from corruption noticed weeks later. The policy is grandfather-father-son: fine granularity recently, coarse granularity further back.

| Tier | Kept | Purpose |
|------|------|---------|
| Most recent | 3 dumps | Undo an operational mistake made today |
| Daily | newest dump of each of the last 7 days | Recover from a bug shipped this week |
| Weekly | newest dump of each of the last 4 ISO weeks | Recover from damage noticed this month |
| Monthly | newest dump of each of the last 6 months | Long-tail recovery and audit |

A dump is deleted only when it falls outside *every* tier, so the tiers overlap rather than compete. Roughly 20 dumps are retained in a steady state. Two safeguards apply: the most recent dump is never deleted, and files whose names do not match the dump pattern are never touched.

Adjust the tiers with flags on the backup command (`--keep-last`, `--keep-daily`, `--keep-weekly`, `--keep-monthly`).

## Installing the schedule

The units are installed by the Ansible role. The `backup` tag applies only the backup tasks, so the schedule can be installed or adjusted without the full setup run's package installs, git pull, and migrations:

```bash
ansible-playbook -i ansible/inventory/production.yml ansible/deploy.yml --tags backup
```

This needs the host to already run a build that contains `metaseed_hub.backup`. The host deploys **release tags**, not `main` (see `deploy.sh`), so the module arrives with the next `v*` tag rather than when the change merges. Installing the timer before that leaves it failing nightly on a missing module.

## Schedule

`metaseed-backup.timer` runs `metaseed-backup.service` daily at 02:30 UTC with `Persistent=true`, so a host that was down at 02:30 takes its backup once it comes back rather than skipping the day.

Check it:

```bash
systemctl list-timers metaseed-backup.timer
journalctl -u metaseed-backup.service -n 50
```

## Running a backup by hand

```bash
sudo -u app /app/.venv/bin/python -m metaseed_hub.backup --directory /var/backups/metaseed-hub
```

The command dumps, verifies the dump is readable with `pg_restore --list`, and only then prunes. A failed or unreadable dump aborts before pruning, so a broken backup run never destroys good dumps.

Pass `--prune-only` to apply retention without taking a new dump, or `--dry-run` to print what would be deleted.

## Restoring

Restoring replaces live data. Stop the application first so nothing writes during the restore.

```bash
sudo systemctl stop metaseed-hub

# Inspect what a dump contains before using it
pg_restore --list /var/backups/metaseed-hub/metaseed_hub-260727-023000.dump | head

# Restore into the running container's database
docker exec -i metaseed-postgres pg_restore \
  --username metaseed --dbname metaseed_hub \
  --clean --if-exists --no-owner \
  < /var/backups/metaseed-hub/metaseed_hub-260727-023000.dump

sudo systemctl start metaseed-hub
```

`--clean --if-exists` drops the existing objects before recreating them, so the result is the dump's state rather than a merge of the two.

After restoring, run `alembic upgrade head` if the dump predates the currently deployed schema.

## Off-host copies

The dumps live on the same host as the database, which protects against data loss in the application and in PostgreSQL, but not against loss of the host itself. Copying `/var/backups/metaseed-hub` to separate storage on a schedule is left to the host's operators; the directory is safe to read while the timer runs, because each dump is written to a temporary file and renamed into place only once complete.
