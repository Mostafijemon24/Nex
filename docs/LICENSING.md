# Licensing notes

## Project license

- SPDX: `GPL-3.0-only` (owner selected GPL-3.0; README and metadata use version **3 only**, not “or later”)
- Full text: [`LICENSE`](../LICENSE) (GNU General Public License version 3)
- Copyright holder for notices in this repository: **Mostafijemon24** (identity taken from the owner-approved GitHub destination `Mostafijemon24/Nex`)
- Copyright year recorded for notices: **2026** (year of this release preparation)

### Official-text verification

Compared against `https://ftp.gnu.org/gnu/Licenses/gpl-3.0.txt` (FSF/GNU FTP mirror of GPL v3).  
`https://www.gnu.org/licenses/gpl-3.0.txt` timed out from this environment.

- Before correction: local file matched choosealicense.com body but differed from GNU FTP by **one line** (missing leading spaces on the title line).
- After correction: `LICENSE` is **byte-identical** to the GNU FTP file (SHA-256 `3972dc9744f6499f0f9b2dbf76696f2ae7ad8af9b23dde66d6af86c9dfb36986`, 674 lines).

## Third-party dependencies

Major application dependencies use permissive licenses (examples: MIT, Apache-2.0, BSD) as published by their upstream projects, including FastAPI/Starlette, SQLAlchemy, Celery, httpx, Next.js, React, Playwright, and related packages.

Distributing this application as a combined work under GPL-3.0 does **not** relicense those upstream components by themselves. Their original notices and licenses remain in their packages. Operators who redistribute binaries or containers must still respect upstream license texts bundled with those dependencies.

## Unresolved / operator responsibilities

1. Confirm no private proprietary code was copied into this tree under incompatible terms (owner responsibility).
2. Container images may include Chromium and ffmpeg; review those image licenses before commercial redistribution.
3. WordPress, OpenRouter, and other remote services remain under their own terms of use; API keys and usage are not covered by GPL-3.0.
