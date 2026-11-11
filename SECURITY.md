# Security

## Credentials

Passwords are hashed with Argon2. Website passwords, extra login fields, AI keys, WordPress application passwords, and S3 secrets are encrypted with Fernet before they are stored. The encryption key comes from `CREDENTIAL_ENCRYPTION_KEY` and is not written into the repository by the application. API responses return a short hint, not the secret.

Sessions are random tokens. The database stores only their SHA-256 hash. The cookie is HttpOnly. Use `COOKIE_SECURE=true` and `COOKIE_SAMESITE=none` only for a cross-site frontend on HTTPS.

## Access control

Every project query checks the session user's organization. A missing project and a project in another organization both return 404. Creating users and publishing require an owner or admin membership.

## Browser and network

Automation accepts only `http` and `https`. Userinfo in URLs is rejected. Hostnames are resolved and blocked when any address is link-local, multicast, reserved, or a known metadata name. Private and loopback addresses are blocked unless `ALLOW_LOCAL_TARGETS=true`. Production startup refuses that flag.

The Chromium process inside the API and browser-worker images defaults to `--no-sandbox` through `CHROMIUM_NO_SANDBOX=true` because the container does not give Chromium the privileges its setuid sandbox needs. Set `CHROMIUM_NO_SANDBOX=false` on hosts that can run the sandbox. Discovery builds a launch-time pin map, passes Chromium `--host-resolver-rules` so named hosts resolve only to validated IPs (with `MAP * ~NOTFOUND` fail-closed for other names), and re-checks every request against that map before `route.continue_()`. Application HTTP clients additionally use `app.core.outbound` TCP pinning.

## Uploads

ZIP members are rejected when the path is absolute, contains `..`, or is a symlink. Expansion stops at the configured file count, uncompressed size, and compression ratio. The analysis reads text and does not import or execute the uploaded project. Git hooks are pointed at `/dev/null` during clone.

## Logs and documents

Structured logs and generated documents pass through the redaction helper for common secret field names and `key=value` assignments. Do not treat redaction as a substitute for keeping secrets out of the samples you upload.

## Retention

Deleting a project removes its database rows through foreign keys and deletes the stored files the API knows about. Backup archives are the operator's copy.

## Known limitations

- Application HTTP clients pin TCP to a validated resolved IP (Host/SNI keep the original hostname) and revalidate every redirect Location.
- Chromium discovery pins named hosts with `--host-resolver-rules` and aborts requests outside the pin map. Container Chromium may still run with `--no-sandbox` when `CHROMIUM_NO_SANDBOX=true`.
- `git clone` accepts only http(s), dual-resolves before clone, disables HTTP redirects and credential helpers, and disables hooks. Git’s TLS stack still performs its own DNS for the hostname (not IP-pinned like application HTTP). Prefer host firewall / egress controls around workers for defense in depth.
- Provider URLs are checked with the same network policy and cannot carry an API key in the query string. The API is not an open HTTP proxy: callers cannot supply arbitrary headers.
- Speech synthesis is optional. Missing audio is not replaced with a file that claims to contain narration.
- The default worker can run Celery beat for scheduled rescans. A schedule does not start a second job while one is unfinished.
- The API container runs as the image's `pwuser`, while the browser process still uses `--no-sandbox`.
- Live OpenRouter, Gemini, OpenAI, and speech calls were not executed in the automated suite. Those tests used local mocks or no network call.
