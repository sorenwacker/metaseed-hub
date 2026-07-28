# Analytics (Matomo, self-hosted)

Metaseed Hub uses a self-hosted [Matomo](https://matomo.org/) for privacy-friendly usage statistics. It is **cookieless** and **first-party**, so it needs no cookie-consent banner and is not blocked by browser tracking protection.

## Design

- Served **same-origin** at `https://metaseed.ewi.tudelft.nl/matomo/`, so the hub's strict CSP (`script-src 'self'`, `connect-src 'self'`) allows the tracker without any third-party exception.
- The Matomo container is bound to **loopback only** (`127.0.0.1:8081`). nginx exposes just the tracker under `/matomo/` (the prefix is stripped, so the container serves `matomo.php` / `matomo.js` at its root). The **admin UI is never public** — reach it through an SSH tunnel.
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

## Viewing statistics

Tunnel as in step 1 and open `http://localhost:8081/`. The dashboard is not exposed publicly by design.
