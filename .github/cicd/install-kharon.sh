#!/usr/bin/env bash
# Build and install Kharon extender inside the adaptixc2 container.
# The repo is cloned to /app/userextenders/kharon by Testing-Kit.
set -euo pipefail

KHARON_DIR=/app/userextenders/kharon

if ! command -v go &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq golang-go make
elif ! command -v make &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq make
fi

echo "Using Go: $(go version)"

# Kharon's go.mod pins axc2 v1.1.3 but the running adaptixc2 binary
# uses v1.2.0. Go plugin ABI requires the exact same axc2 version;
# mismatches cause adaptixc2 to crash on plugin load.
AXC2_VERSION=v1.2.0
echo "Pinning axc2 to ${AXC2_VERSION}"

echo "Building Kharon listener..."
cd "${KHARON_DIR}/listener_kharon_http"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
make all

echo "Building Kharon agent plugin..."
cd "${KHARON_DIR}/agent_kharon"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
make plugin

echo "Kharon build complete."
