# Release notes draft (unreleased)

**Status:** NOT READY FOR PUBLIC RELEASE — staging deploy and live OpenRouter verification still required.

**Intended public remote:** `https://github.com/Mostafijemon24/Nex` (verified empty as of preparation; no push performed while mandatory gates remain).

## License

- Added GNU GPL version 3 text in `LICENSE` (`GPL-3.0-only`).
- Copyright notice: Mostafijemon24, 2026 (from owner-approved GitHub identity).

## Security (local)

- Application HTTP TCP pin + redirect revalidation.
- Chromium host-resolver pinning + pin-map request guard.
- Git clone http(s)-only, dual resolve, redirects/helpers disabled.
- Residual: container `CHROMIUM_NO_SANDBOX` default; Git TLS DNS not IP-pinned — document egress controls on Hostinger workers.

## Staging / live AI

- Hostinger VPS selected by owner; procedure in `docs/STAGING.md`.
- OpenRouter selected for mandatory live verification; `scripts/live-openrouter-check.sh` prepared (BLOCKED until key on staging host).

## Blocked before READY

1. Hostinger hostname/SSH + explicit deploy authorization.
2. OpenRouter live generation PASS on staging.
3. Staging acceptance tests.
4. Then only: configure `origin` and push to the empty GitHub repository (no force-push).
