# Analytics (Matomo, self-hosted)

Metaseed Hub uses a self-hosted [Matomo](https://matomo.org/) for privacy-friendly usage statistics. It is **cookieless** and **first-party**, so it needs no cookie-consent banner and is not blocked by browser tracking protection.

## Design

- Served **same-origin** at `https://metaseed.ewi.tudelft.nl/matomo/`, so the hub's strict CSP (`script-src 'self'`, `connect-src 'self'`) allows the tracker without any third-party exception.
- The Matomo container is bound to **loopback only** (`127.0.0.1:8081`). nginx exposes **only** the two tracker endpoints (`/matomo/matomo.php`, `/matomo/matomo.js`) publicly. The installer and admin UI are not routed publicly (they 404), so the PHP admin surface is never on the internet. The **admin UI is never public** — reach it through an SSH tunnel.
- The tracker renders only when `MATOMO_SITE_ID` is set, so dev and CI stay clean.

## First-run setup (once, after the containers are up)

1. Tunnel to the admin UI:
   ```
   ssh -L 8081:127.0.0.1:8081 metaseed.ewi
   ```
   Open `http://localhost:8081/` and complete the install wizard (it already has the database env vars, so accept them). Create an admin user, then a website named "Metaseed Hub" with URL `https://metaseed.ewi.tudelft.nl` — note the **site id** it assigns (usually `1`).

2. Make it cookieless and privacy-respecting, in the Matomo admin:
   - **Administration → Privacy → Anonymize data**: anonymize visitors' IP (at least 2 bytes), and enable "Anonymize the Order ID" / respect Do Not Track.
   - The tracker JS already calls `disableCookies`, so no consent banner is required.

3. Allow the public host for tracking. In the container's `config/config.ini.php` add under `[General]`:
   ```
   trusted_hosts[] = "metaseed.ewi.tudelft.nl"
   trusted_hosts[] = "localhost:8081"
   ```

4. Set the site id and redeploy the hub config:
   - Set `matomo_site_id` in `ansible/group_vars/all.yml` (or the env) to the id from step 1, and re-run the playbook (or edit `/app/.env` `MATOMO_SITE_ID` and restart the service).

## Disabled until PHP2Shell is fixed (since 261021)

Matomo is a PHP application, and nginx routes `/matomo/matomo.php` publicly. PHP2Shell (CVE-2026-109618, CVE-2026-109619) is a pair of critical remote code execution flaws in PHP; the reported fix for the 8.4 branch is PHP 8.4.27. On 261021 the deployed `matomo:5.13.0-apache` ran PHP 8.4.25, and no released matomo image carried 8.4.27 (`5.14.0-apache` ships 8.4.26).

The `matomo` service in the production compose file therefore carries the profile `php2shell-disabled`. `docker compose up -d`, which both the container update script and the deploy run, starts only the services without a profile, so Matomo stays down across deploys and reboots. The hub is unaffected; the tracker requests fail and no visits are recorded. The data in `matomo-db` is kept.

To re-enable, once a matomo image with PHP 8.4.27 or later is released:

1. Check the PHP version of the candidate image: `docker run --rm --entrypoint php matomo:<tag> -r 'echo PHP_VERSION;'`.
2. In `ansible/roles/metaseed-hub/files/docker-compose.yml`, set the `matomo` image to that tag and remove the `profiles` line.
3. Remove the gate `test_matomo_stays_off_until_php2shell_is_fixed` in `tests/test_container_updates.py`.
4. Merge. The next deploy recreates the container, and the update script syncs the application files and runs `core:update`.

## Why the dashboard is tunnel-only

Matomo does not run cleanly behind a stripped-prefix reverse proxy: served under a subpath it generates root-relative URLs for its dynamic assets, so the logo and the login break. Only the two tracker endpoints (`matomo.php`, `matomo.js`) are exposed publicly, same-origin. The dashboard is reached through an SSH tunnel, where Matomo runs at its own root exactly as it expects.

## Viewing statistics

Tunnel as in step 1 and open `http://localhost:8081/`. The dashboard is not exposed publicly by design.
