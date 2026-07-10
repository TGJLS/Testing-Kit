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

echo "Building Kharon listener..."
cd "${KHARON_DIR}/listener_kharon_http"
make all

echo "Building Kharon agent plugin..."
cd "${KHARON_DIR}/agent_kharon"
make plugin

echo "Kharon build complete."
