#!/usr/bin/env bash
# Build and install Kharon extender inside the adaptixc2 container.
# The repo is already cloned to /app/extenders/kharon by Testing-Kit.
set -euo pipefail

KHARON_DIR=/app/extenders/kharon

if ! command -v go &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq golang-go
fi

GO_VERSION=$(go version | awk '{print $3}')
echo "Using Go: ${GO_VERSION}"

echo "Building Kharon listener..."
cd "${KHARON_DIR}"
if [[ -f Makefile ]]; then
    make listener
else
    cd "${KHARON_DIR}/listener_kharon_http"
    go build -buildmode=plugin -trimpath -o listener.so .
fi

echo "Building Kharon agent..."
cd "${KHARON_DIR}"
if [[ -f Makefile ]]; then
    make agent
else
    cd "${KHARON_DIR}/agent_kharon"
    go build -buildmode=plugin -trimpath -o agent.so .
fi

echo "Kharon build complete."
