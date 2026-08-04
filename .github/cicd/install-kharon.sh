#!/usr/bin/env bash
# Build and install Kharon extender inside the adaptixc2 container.
# The repo is cloned to /app/userextenders/kharon by Testing-Kit.
set -euo pipefail

KHARON_DIR=/app/userextenders/kharon

NEED_PKGS=()
command -v make    &>/dev/null || NEED_PKGS+=(make)
command -v python3 &>/dev/null || NEED_PKGS+=(python3)
command -v git     &>/dev/null || NEED_PKGS+=(git)
command -v nasm    &>/dev/null || NEED_PKGS+=(nasm)
command -v clang   &>/dev/null || NEED_PKGS+=(clang llvm)
if [ ${#NEED_PKGS[@]} -gt 0 ]; then
    apt-get update -qq
    apt-get install -y -qq "${NEED_PKGS[@]}"
fi

# The container's Go binary is the same one that compiled adaptixserver
# (copied from the build stage in the Dockerfile), so compiler binary hashes
# match automatically. GOEXPERIMENT is set as an env var in the image.
echo "Using Go: $(go version)"
[ -n "${GOEXPERIMENT:-}" ] && echo "GOEXPERIMENT: ${GOEXPERIMENT}"
BINARY_GOEXP="${GOEXPERIMENT:-}"

# --- Read server build info ---
SERVER_BUILD_INFO=$(go version -m /app/adaptixserver 2>/dev/null) || SERVER_BUILD_INFO=""

# Extract axc2 version from server binary.  Fall back to v1.2.0 if not found.
BINARY_AXC2=$(echo "$SERVER_BUILD_INFO" | \
    awk '/github\.com\/Adaptix-Framework\/axc2/{print $3}') || BINARY_AXC2=""
if [[ "${BINARY_AXC2}" =~ ^v[0-9] ]]; then
    AXC2_VERSION="${BINARY_AXC2}"
else
    AXC2_VERSION=v1.2.0
fi
echo "Pinning axc2 to ${AXC2_VERSION}"

# --- Register Kharon in AdaptixServer's go.work ---
# Building from within the same workspace as adaptixserver ensures Kharon
# plugins share the EXACT same dep graph (same MVS resolution).  The image
# also bundles the Go build cache from the server build stage, so Go finds
# the pre-compiled axc2 package and reuses it instead of recompiling.
# Reusing the cached .a means the build ID embedded in the plugin matches
# the one already loaded by adaptixserver → plugin.Open succeeds.
AXC2_SRC=/app/adaptixc2-src/AdaptixServer
AXC2_GOWORK="${AXC2_SRC}/go.work"
cd "${AXC2_SRC}"
go work use "${KHARON_DIR}/listener_kharon_http"
go work use "${KHARON_DIR}/agent_kharon"

# --- Build Kharon listener ---
echo "Building Kharon listener..."
cd "${KHARON_DIR}/listener_kharon_http"
GOWORK="${AXC2_GOWORK}" GOEXPERIMENT="${BINARY_GOEXP}" GONOSUMDB='*' make all

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
    print('ERROR: patch target not found in pl_agent.go — upstream may have changed the switch structure', file=sys.stderr)
    sys.exit(1)
" "$AGENT_GO"

# --- Build Kharon agent plugin ---
echo "Building Kharon agent plugin..."
rm -f "${KHARON_DIR}/agent_kharon/dist/agent_kharon.so"
cd "${KHARON_DIR}/agent_kharon/src_server"
GOWORK="${AXC2_GOWORK}" GOEXPERIMENT="${BINARY_GOEXP}" GONOSUMDB='*' \
    go build -buildmode=plugin -o "../dist/agent_kharon.so" .
echo "Built: $(ls -sh ../dist/agent_kharon.so)"

# --- Build src_beacon BOF prerequisites ---
echo "Building src_beacon prerequisites (nasm, LLVM object files)..."
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
fi

echo "Kharon build complete."
