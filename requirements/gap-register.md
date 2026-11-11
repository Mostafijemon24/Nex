# Release gap register

## Owner decisions recorded

| Decision | Value |
|---|---|
| License | GPL-3.0 (`GPL-3.0-only`) |
| Staging | Hostinger VPS |
| Live AI for this gate | OpenRouter |
| GitHub | `https://github.com/Mostafijemon24/Nex` (empty; no push yet) |

## Catalog

| Item | Status |
|---|---|
| 32 / 43 ACs | Local `make test` must stay green on the release candidate revision |

## Gates

| ID | Status | Notes |
|---|---|---|
| LICENSE | PASS | `LICENSE` + README SPDX + `docs/LICENSING.md` |
| SECRET_SCAN | PASS when script exits 0 on clean tree |
| OPENROUTER_MOCK | PASS | Catalog / adapter mocks in pytest |
| OPENROUTER_LIVE | BLOCKED | Needs key on Hostinger staging; use UI or `scripts/live-openrouter-check.sh` |
| HOSTINGER_STAGING | BLOCKED | Needs IP/hostname, SSH/Docker Manager access, deploy authorization |
| STAGING_ACCEPTANCE | BLOCKED | Depends on staging |
| GITHUB_PUSH | BLOCKED | Remote may be added; public push deferred until mandatory gates pass |
| CHROMIUM_SANDBOX | LIMITATION | Do not flip `CHROMIUM_NO_SANDBOX` without Hostinger browser smoke test |
| GIT_DNS_PIN | LIMITATION | Prefer Hostinger firewall egress for workers |
