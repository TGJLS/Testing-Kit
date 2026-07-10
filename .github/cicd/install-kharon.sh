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

# Pin axc2 to the version that matches the running adaptixc2 binary.
# Go plugin ABI requires exact package version match; axc2 v1.1.3 (Kharon
# default) will fail to load against an adaptixc2 built with v1.2.0.
AXC2_VERSION=$(go version -m /app/adaptixc2 2>/dev/null | awk '/github.com\/Adaptix-Framework\/axc2/{print $3}')
AXC2_VERSION="${AXC2_VERSION:-v1.2.0}"
echo "Pinning axc2 to ${AXC2_VERSION} (matches adaptixc2 binary)"

echo "Building Kharon listener..."
cd "${KHARON_DIR}/listener_kharon_http"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
make all

echo "Building Kharon agent plugin..."
cd "${KHARON_DIR}/agent_kharon"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
make plugin

echo "Kharon build complete."
