# Operating system updates

The deployment host runs Ubuntu 24.04. Its packages are updated by `unattended-upgrades`, which `apt-daily-upgrade.timer` runs once a day between 06:00 and 07:00. The host clock is Europe/Amsterdam, and every time on this page is host time. The containers are covered separately, in [Container updates](container-updates.md).

## Two configuration files

`/etc/apt/apt.conf.d/50unattended-upgrades` is the host's base configuration. It predates this repository's management of the host and may be maintained by the university's IT department, so ansible does not write it. It allows the security pockets (`noble-security`, ESM apps and infra security), and enables automatic reboots.

`/etc/apt/apt.conf.d/52metaseed-unattended-upgrades` is written by ansible from `ansible/roles/metaseed-hub/templates/unattended-upgrades.conf.j2`. apt reads both files in order; a list such as `Unattended-Upgrade::Allowed-Origins` accumulates across files, and a scalar set in the later file overrides the earlier one. The file adds:

- `noble-updates` to the allowed origins. Ubuntu publishes most fixes there, including systemd, sssd and the kernel meta-packages; on 261124 all 40 pending packages came from `noble-updates` and none from `noble-security`, so a security-only policy left them for manual upgrades.
- `Unattended-Upgrade::Automatic-Reboot "true"` and `Automatic-Reboot-Time "07:30"`. A kernel or libc update sets `/var/run/reboot-required`; the host then reboots at 07:30 on the same morning instead of the moment the daily run finishes. The time has to fall after the upgrade window: an earlier time, such as 04:45, is already past when the upgrade finishes, and the reboot would wait until the next day.

Automatic reboots restart the hub. It is down for about a minute while the host boots and the hub waits for PostgreSQL; nginx answers 502 in that time.

## Applying the configuration

The file is part of the `setup` and `os-updates` ansible tags. A release tag does not touch it. Run from the `ansible/` directory:

```bash
ansible-playbook deploy.yml --tags os-updates
```

## Checking what happened

```bash
cat /var/log/unattended-upgrades/unattended-upgrades.log
apt-config dump | grep -i unattended
last -x reboot | head
```

`apt-config dump` shows the merged configuration, so it confirms that both files are read.
