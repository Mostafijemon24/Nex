#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -f .env ]; then
  cp .env.example .env
fi
python3 - <<'PY'
from pathlib import Path
import base64, secrets
path = Path(".env")
text = path.read_text()
replacements = {
    "SECRET_KEY": secrets.token_urlsafe(32),
    "CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
}
lines = []
for line in text.splitlines():
    name, separator, value = line.partition("=")
    if separator and name in replacements and value.strip() == "":
        lines.append(f"{name}={replacements[name]}")
    else:
        lines.append(line)
path.write_text("\n".join(lines) + "\n")
print("Environment file is ready. Existing secret values were left unchanged.")
PY
