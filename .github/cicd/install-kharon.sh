#!/usr/bin/env bash
# Build and install Kharon extender inside the adaptixc2 container.
# The repo is cloned to /app/userextenders/kharon by Testing-Kit.
set -euo pipefail

KHARON_DIR=/app/userextenders/kharon

NEED_PKGS=()
command -v make    &>/dev/null || NEED_PKGS+=(make)
command -v python3 &>/dev/null || NEED_PKGS+=(python3)
if [ ${#NEED_PKGS[@]} -gt 0 ]; then
    apt-get update -qq
    apt-get install -y -qq "${NEED_PKGS[@]}"
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

# --- Inspect adaptixserver binary ---
# Pin ALL dep versions from the server binary so every shared package in the
# plugin has an IDENTICAL build ID to the one already compiled into the server.
# The axc2 h1: hash alone is not sufficient — axc2's transitive deps (x/sys,
# x/text, …) also affect build IDs, and any version delta causes:
#   plugin.Open: "plugin was built with a different version of package X"
echo "=== adaptixserver build info ==="
SERVER_BUILD_INFO=$(go version -m /app/adaptixserver 2>/dev/null) || SERVER_BUILD_INFO=""
if [ -z "$SERVER_BUILD_INFO" ]; then
    echo "(go version -m failed)"
else
    echo "$SERVER_BUILD_INFO"
fi

# Extract all dep versions: "module/path@vX.Y.Z"
SERVER_DEPS_FILE=/tmp/server_deps.txt
echo "$SERVER_BUILD_INFO" | awk '/^\tdep\t/{print $2"@"$3}' > "$SERVER_DEPS_FILE"
echo "Found $(wc -l < "$SERVER_DEPS_FILE" | tr -d ' ') dep modules in server binary"

# Extract the exact axc2 version.  Fall back to v1.2.0 if not found.
BINARY_AXC2=$(echo "$SERVER_BUILD_INFO" | \
    awk '/github\.com\/Adaptix-Framework\/axc2/{print $3}') || BINARY_AXC2=""
echo "axc2 in binary: ${BINARY_AXC2:-unknown}"
if [[ "${BINARY_AXC2}" =~ ^v[0-9] ]]; then
    AXC2_VERSION="${BINARY_AXC2}"
else
    AXC2_VERSION=v1.2.0
fi
echo "Pinning axc2 to ${AXC2_VERSION}"

# --- Build combined go.work ---
# Include ONLY Kharon modules so AdaptixC2 HEAD doesn't bump deps via MVS.
COMBINED_WORK=/tmp/combined.work
GO_WORK_VER="${BINARY_GO#go}"
[ -z "$GO_WORK_VER" ] && GO_WORK_VER="1.25"
{
    printf 'go %s\n\nuse (\n' "${GO_WORK_VER}"
    printf '    %s\n' "${KHARON_DIR}/listener_kharon_http"
    printf '    %s\n' "${KHARON_DIR}/agent_kharon"
    printf ')\n'
} > "$COMBINED_WORK"

echo "=== Generated go.work ==="
cat "$COMBINED_WORK"

# Pin ALL server dep versions into a module's go.mod so MVS selects identical
# versions for every shared package.
pin_server_deps() {
    local dir="$1"
    echo "Pinning server dep versions in $(basename "$dir")..."
    (cd "$dir" && while IFS= read -r dep; do
        go mod edit -require "$dep" 2>/dev/null || true
    done < "$SERVER_DEPS_FILE")
    # go mod download is exempt from -mod=readonly and can update go.sum without
    # the -mod=mod flag.  Run it visibly so CI logs show any download failures.
    echo "Downloading pinned modules to update go.sum..."
    (cd "$dir" && GOWORK="${COMBINED_WORK}" GONOSUMDB='*' go mod download 2>&1 || true)
}

# --- Build Kharon listener ---
echo "Building Kharon listener..."
echo "=== listener_kharon_http/Makefile ==="
cat "${KHARON_DIR}/listener_kharon_http/Makefile" 2>/dev/null || echo "(no Makefile)"
cd "${KHARON_DIR}/listener_kharon_http"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
pin_server_deps "${KHARON_DIR}/listener_kharon_http"
GOWORK="${COMBINED_WORK}" GOEXPERIMENT="${BINARY_GOEXP}" GONOSUMDB='*' make all

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

# --- Build Kharon agent plugin ---
echo "Building Kharon agent plugin..."
cd "${KHARON_DIR}/agent_kharon"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
pin_server_deps "${KHARON_DIR}/agent_kharon"
rm -f dist/agent_kharon.so
cd "${KHARON_DIR}/agent_kharon/src_server"
GOWORK="${COMBINED_WORK}" GOEXPERIMENT="${BINARY_GOEXP}" GONOSUMDB='*' \
    go build -buildmode=plugin -o "../dist/agent_kharon.so" .
echo "Built: $(ls -sh ../dist/agent_kharon.so)"

echo "=== axc2 version in built plugins ==="
go version -m "${KHARON_DIR}/listener_kharon_http/dist/listener_kharon_http.so" 2>/dev/null | \
    awk '/axc2/{print "  listener: "$0}' || echo "  listener: (read failed)"
go version -m "${KHARON_DIR}/agent_kharon/dist/agent_kharon.so" 2>/dev/null | \
    awk '/axc2/{print "  agent:    "$0}' || echo "  agent: (read failed)"
echo "  server:   $(echo "$SERVER_BUILD_INFO" | awk '/axc2/{print $0}')"

# --- Diagnostic: compare key dep versions across server and plugins ---
echo "=== Key dep versions in built artifacts ==="
for artifact in \
    "/app/adaptixserver" \
    "${KHARON_DIR}/listener_kharon_http/dist/listener_kharon_http.so" \
    "${KHARON_DIR}/agent_kharon/dist/agent_kharon.so"
do
    echo "  $(basename "$artifact"):"
    go version -m "$artifact" 2>/dev/null | \
        awk '/golang\.org\/x\/sys|golang\.org\/x\/text|Adaptix-Framework\/axc2/{printf "    %s\n", $0}' \
        || echo "    (read failed)"
done

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
