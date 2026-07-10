#!/usr/bin/env bash
# Install Extension-Kit BOF collection inside adaptixc2.
# The repo is cloned to /app/extenders/extension-kit.
set -euo pipefail

EXT_KIT_DIR=/app/userextenders/extension-kit

echo "Extension-Kit: checking for pre-built BOF files..."

if [[ -f "${EXT_KIT_DIR}/setup.sh" ]]; then
    bash "${EXT_KIT_DIR}/setup.sh"
elif [[ -f "${EXT_KIT_DIR}/install.sh" ]]; then
    bash "${EXT_KIT_DIR}/install.sh"
else
    echo "No setup script found — BOF files assumed pre-compiled."
fi

echo "Extension-Kit install complete."
