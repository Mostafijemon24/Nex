#!/usr/bin/env bash
# Fail if likely secrets appear in tracked files. Does not scan .env (gitignored).
set -euo pipefail
cd "$(dirname "$0")/.."
patterns='(AKIA[0-9A-Z]{16}|-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----|sk-live-[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})'
if git ls-files -z | xargs -0 grep -nE "$patterns" 2>/dev/null; then
  echo "Potential secrets found in tracked files." >&2
  exit 1
fi
echo "No high-confidence secret patterns found in tracked files."
