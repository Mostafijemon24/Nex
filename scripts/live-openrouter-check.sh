#!/usr/bin/env bash
# Live OpenRouter generation check. Never prints the API key.
# Usage on staging (key already in the environment or Compose secrets):
#   OPENROUTER_API_KEY=… ./scripts/live-openrouter-check.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "BLOCKED: OPENROUTER_API_KEY is not set. Configure it on the staging host; do not paste keys into chat or git."
  exit 2
fi

REV="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
ENDPOINT="${OPENROUTER_ENDPOINT:-https://openrouter.ai/api/v1}"
MODEL="${OPENROUTER_MODEL:-openrouter/auto}"

# Validate endpoint host without logging secrets.
python3 - "$ENDPOINT" "$MODEL" "$REV" <<'PY'
import json, os, sys, urllib.error, urllib.request
from urllib.parse import urlparse

endpoint, model, rev = sys.argv[1:4]
key = os.environ["OPENROUTER_API_KEY"]
parsed = urlparse(endpoint)
if parsed.scheme != "https" or parsed.hostname not in {"openrouter.ai", "api.openrouter.ai"}:
    print(f"FAIL: refusing unexpected OpenRouter endpoint host {parsed.hostname!r}")
    sys.exit(1)
url = endpoint.rstrip("/") + "/chat/completions"
body = json.dumps({
    "model": model,
    "messages": [
        {"role": "system", "content": "Reply with exactly: openrouter-live-ok"},
        {"role": "user", "content": "ping"},
    ],
    "max_tokens": 32,
}).encode()
req = urllib.request.Request(
    url,
    data=body,
    headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "HTTP-Referer": os.environ.get("PUBLIC_APP_URL", "https://localhost"),
        "X-Title": "PRS3-live-openrouter-check",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=45) as resp:
        payload = json.loads(resp.read().decode())
        status = resp.status
except urllib.error.HTTPError as exc:
    detail = exc.read().decode(errors="replace")[:200]
    print(f"FAIL: HTTP {exc.code} from OpenRouter (revision {rev}). Body redacted length={len(detail)}")
    sys.exit(1)
except Exception as exc:
    print(f"FAIL: {exc.__class__.__name__} (revision {rev})")
    sys.exit(1)

text = ""
try:
    text = payload["choices"][0]["message"]["content"]
except Exception:
    print(f"FAIL: unexpected response shape (revision {rev})")
    sys.exit(1)
if "openrouter-live-ok" not in text and not text.strip():
    print(f"FAIL: empty or unexpected generation (revision {rev})")
    sys.exit(1)
print(f"PASS: live OpenRouter generation HTTP {status} (revision {rev}, model={model})")
print("NOTE: This script is a live provider check, not a catalog mock test.")
PY
