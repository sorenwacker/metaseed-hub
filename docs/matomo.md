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

## PHP2Shell (261021-261022)

Matomo is a PHP application, and nginx routes `/matomo/matomo.php` publicly. PHP2Shell (CVE-2026-109618, CVE-2026-109619) is a pair of critical remote code execution flaws in PHP, fixed in PHP 8.4.27 for the 8.4 branch. The deployed `matomo:5.13.0-apache` ran PHP 8.4.25.

On 261021 Matomo was stopped on the host and the `matomo` service got the compose profile `php2shell-disabled`, so that `docker compose up -d` would skip it. The live compose file is replaced from `main` by every deploy, and a deploy at 261021 16:05 ran before the profile was merged; it recreated Matomo on 5.13.0, which then served until 261022.

On 261022 the docker library rebuilt `matomo:5.14.0-apache` on PHP 8.4.27. The production compose file pins that tag and the profile is gone. `test_matomo_pin_carries_the_php2shell_fix` fails if the pin falls below 5.14.0, the first matomo release whose apache image carries the fix; check a new tag's PHP before pinning it:

```
docker run --rm --entrypoint php matomo:<tag> -r 'echo PHP_VERSION;'
```

The same tag was rebuilt on 261021 with PHP 8.4.26 and on 261022 with 8.4.27, and a deploy runs `docker compose up -d` without a pull, so a host that pulled `5.14.0-apache` before 261022 05:11 UTC would keep the vulnerable build. The image is therefore pinned by digest as well as tag (`matomo:5.14.0-apache@sha256:40680f8e…`, PHP 8.4.27 on amd64 and arm64): a digest names one build, which the host fetches if it does not hold it. `test_matomo_is_pinned_to_the_build_that_carries_the_fix` fails if the digest is dropped. Renovate updates the digest with each rebuild; check the new build's PHP as above before merging that PR.

## Why the dashboard is tunnel-only

Matomo does not run cleanly behind a stripped-prefix reverse proxy: served under a subpath it generates root-relative URLs for its dynamic assets, so the logo and the login break. Only the two tracker endpoints (`matomo.php`, `matomo.js`) are exposed publicly, same-origin. The dashboard is reached through an SSH tunnel, where Matomo runs at its own root exactly as it expects.

## Viewing statistics

Tunnel as in step 1 and open `http://localhost:8081/`. The dashboard is not exposed publicly by design.
