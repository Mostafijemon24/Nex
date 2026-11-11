# Rollback: n8n.avonixai.com → previous Social Blast

**Status:** Procedure documented from the pre-replacement inventory. After first cutover, treat a restore as operationally tested only once it has been executed successfully.

## What was replaced

- Public site at `https://n8n.avonixai.com/` previously proxied to PM2 `social-blast` on `127.0.0.1:3055`.
- Apache userdata proxy: `.../n8navonixai/n8n.avonixai.com/postmill-proxy.conf`
- PRS 3.0 intended target: Docker web on `127.0.0.1:3060` under `/opt/prs-nex`.

## Backup location (on VPS)

Timestamped directory under `/root/backups/n8n-domain-<UTC-timestamp>/` containing:

- Apache SSL/STD proxy configs (and prior `.bak-*` copies)
- `/var/www/social-blast` application tree (excluding `node_modules` if omitted to save space — prefer full tree when disk allows)
- PM2 dump / process description for `social-blast`
- Notes file listing unrelated services that must not be deleted

## Restore steps

1. Confirm PRS downtime is acceptable.
2. Restore Apache SSL proxy file from the backup (or the `*.bak-socialblast-*` copy) so `ProxyPass` points to `http://127.0.0.1:3055/`.
3. `cd /usr/local/cpanel` or run `/scripts/rebuildhttpdconf && /scripts/restartsrv_httpd` as appropriate on this Hostinger AlmaLinux/cPanel host.
4. Ensure PM2 `social-blast` is online: `pm2 start social-blast` / `pm2 save`.
5. Optionally stop PRS stack: `cd /opt/prs-nex && docker compose -f docker-compose.yml -f docker-compose.hostinger.yml down` (**do not** use `-v` unless intentionally wiping PRS volumes).
6. Verify `https://n8n.avonixai.com/` shows Social Blast again.

## Do not

- Delete `/opt/n8n` volumes or other PM2 apps (`avonix-ai-web`, cadence, etc.) when rolling back.
- Restore a database dump over a newer live database without reviewing data loss.
