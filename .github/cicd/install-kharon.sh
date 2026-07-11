#!/usr/bin/env bash
# Build and install Kharon extender inside the adaptixc2 container.
# The repo is cloned to /app/userextenders/kharon by Testing-Kit.
set -euo pipefail

KHARON_DIR=/app/userextenders/kharon

if ! command -v make &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq make
fi

# --- Go toolchain ---
# Go plugins require the EXACT same Go toolchain version AND GOEXPERIMENT flags
# as the main adaptixserver binary.  Read both from the binary.
BINARY_GO=$(go version -m /app/adaptixserver 2>/dev/null | awk 'NR==1{print $2}') || BINARY_GO=""
CURRENT_GO=$(go version 2>/dev/null | awk '{print $3}') || CURRENT_GO=""
BINARY_GOEXP=$(go version -m /app/adaptixserver 2>/dev/null | awk 'NR==1{sub(/^.*X:/,""); print}') || BINARY_GOEXP=""

if [ -n "$BINARY_GO" ] && [ "$BINARY_GO" != "$CURRENT_GO" ]; then
    echo "Toolchain mismatch: adaptixserver=${BINARY_GO}, container=${CURRENT_GO}"
    echo "Installing ${BINARY_GO} at /usr/local/go..."
    wget -qO- "https://dl.google.com/go/${BINARY_GO}.linux-amd64.tar.gz" | \
        tar -xz -C /usr/local/ --overwrite
    echo "Now using: $(go version)"
fi

echo "Using Go: $(go version)"
echo "GOEXPERIMENT: ${BINARY_GOEXP}"

# --- axc2 version ---
# Kharon's go.mod may pin an older axc2 version; Go plugin ABI requires
# the exact same axc2 as the running server.
AXC2_VERSION=v1.2.0
echo "Pinning axc2 to ${AXC2_VERSION}"

# --- Build Kharon listener ---
echo "Building Kharon listener..."
cd "${KHARON_DIR}/listener_kharon_http"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
GOEXPERIMENT="${BINARY_GOEXP}" make all

# --- Patch pl_agent.go: add mask_sleep="none" -> KH_SLEEP_MASK=0 ---
# Without this, the default sleep mask mode (3) uses obfuscation techniques
# that prevent the agent from beaconing in a plain QEMU VM environment.
AGENT_GO="${KHARON_DIR}/agent_kharon/src_server/pl_agent.go"
python3 -c "
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
old = '''	case \"pooling\":
		makeVars = append(makeVars, \"KH_SLEEP_MASK=2\")
	default:
		makeVars = append(makeVars, \"KH_SLEEP_MASK=3\")'''
new = '''	case \"pooling\":
		makeVars = append(makeVars, \"KH_SLEEP_MASK=2\")
	case \"none\":
		makeVars = append(makeVars, \"KH_SLEEP_MASK=0\")
	default:
		makeVars = append(makeVars, \"KH_SLEEP_MASK=3\")'''
if old in content:
    content = content.replace(old, new)
    with open(path, 'w') as f:
        f.write(content)
    print('Patched pl_agent.go: added KH_SLEEP_MASK=0 for mask_sleep=none')
elif 'case \"none\":' in content:
    print('pl_agent.go already patched')
else:
    print('WARNING: patch target not found in pl_agent.go', file=sys.stderr)
" "$AGENT_GO"

# --- Build combined go.work to resolve shared package versions ---
# Without this, the plugin may embed different package versions than the server,
# causing plugin.Open() to fail with "different version of package" errors.
ADAPTIX_SRC=/tmp/adaptixc2-src
if [ ! -d "$ADAPTIX_SRC" ]; then
    echo "Cloning AdaptixC2 source for go.work..."
    git clone --depth=1 https://github.com/TGJLS/AdaptixC2 "$ADAPTIX_SRC"
fi

COMBINED_WORK=/tmp/combined.work
cat > "$COMBINED_WORK" <<EOF
go 1.25

use (
    ${ADAPTIX_SRC}/AdaptixServer
    ${ADAPTIX_SRC}/AdaptixServer/extenders/beacon_agent
    ${ADAPTIX_SRC}/AdaptixServer/extenders/beacon_listener_dns
    ${ADAPTIX_SRC}/AdaptixServer/extenders/beacon_listener_http
    ${ADAPTIX_SRC}/AdaptixServer/extenders/beacon_listener_smb
    ${ADAPTIX_SRC}/AdaptixServer/extenders/beacon_listener_tcp
    ${ADAPTIX_SRC}/AdaptixServer/extenders/gopher_agent
    ${ADAPTIX_SRC}/AdaptixServer/extenders/gopher_listener_tcp
    ${KHARON_DIR}/listener_kharon_http
    ${KHARON_DIR}/agent_kharon
)
EOF

# --- Build Kharon agent plugin ---
echo "Building Kharon agent plugin..."
cd "${KHARON_DIR}/agent_kharon"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
rm -f dist/agent_kharon.so
cd "${KHARON_DIR}/agent_kharon/src_server"
GOWORK="${COMBINED_WORK}" GOEXPERIMENT="${BINARY_GOEXP}" \
    go build -buildmode=plugin -o "../dist/agent_kharon.so" .
echo "Built: $(ls -sh ../dist/agent_kharon.so)"

# --- Build src_beacon BOF prerequisites ---
echo "Building src_beacon prerequisites (nasm, LLVM object files)..."
if ! command -v nasm &>/dev/null || ! command -v clang &>/dev/null; then
    apt-get update -qq
    apt-get install -y -qq nasm clang llvm
fi
cd "${KHARON_DIR}/agent_kharon/src_beacon"
make prebuild-x64

# --- Build src_core BOF modules ---
# Patch win32.h for types missing from older MinGW SDK headers.
WIN32_H="${KHARON_DIR}/agent_kharon/src_core/include/win32.h"
python3 -c "
import sys
path = sys.argv[1]
with open(path) as f:
    content = f.read()
stub = '''
// Types missing from older MinGW SDK
#ifndef _PROCESS_MITIGATION_USER_POINTER_AUTH_POLICY_DEFINED
#define _PROCESS_MITIGATION_USER_POINTER_AUTH_POLICY_DEFINED
typedef struct { DWORD EnablePointerAuthKernel : 1; DWORD Spare : 31; } PROCESS_MITIGATION_USER_POINTER_AUTH_POLICY;
#endif
#ifndef _PROCESS_MITIGATION_SEHOP_POLICY_DEFINED
#define _PROCESS_MITIGATION_SEHOP_POLICY_DEFINED
typedef struct { DWORD EnableSehop : 1; DWORD Spare : 31; } PROCESS_MITIGATION_SEHOP_POLICY;
#endif
'''
marker = 'typedef struct _PROCESS_MITIGATION_POLICY_INFORMATION'
if stub.strip() not in content:
    content = content.replace(marker, stub + marker)
    with open(path, 'w') as f:
        f.write(content)
    print('Patched win32.h: added missing MinGW type stubs')
else:
    print('win32.h already patched')
" "$WIN32_H"

echo "Building src_core BOF modules..."
cd "${KHARON_DIR}/agent_kharon/src_core"
make all

# --- Set up /dist/extenders/agent_kharon symlinks ---
# adaptixserver looks for src_beacon, src_loader, src_core under
# /dist/extenders/agent_kharon/ at agent-generate time.
DIST_KH=/dist/extenders/agent_kharon
mkdir -p "$DIST_KH"

for dir in src_beacon src_loader src_core; do
    target="${KHARON_DIR}/agent_kharon/${dir}"
    link="${DIST_KH}/${dir}"
    if [ ! -L "$link" ]; then
        ln -sf "$target" "$link"
        echo "Symlink: ${link} -> ${target}"
    fi
done

# --- cstdint shim for clang 14 MinGW Exe format compilation ---
CSTDINT="${KHARON_DIR}/agent_kharon/src_loader/Include/cstdint"
if [ ! -f "$CSTDINT" ]; then
    cat > "$CSTDINT" <<'SHIM'
#pragma once
#include <stdint.h>
#include <stddef.h>
SHIM
    echo "Created cstdint shim at ${CSTDINT}"
fi

echo "Kharon build complete."
