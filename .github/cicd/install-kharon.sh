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

# --- Rebuild adaptixserver from source ---
# Go plugin ABI compatibility requires every shared package to have the SAME
# build ID, which is computed from: source content + dependency build IDs +
# Go COMPILER BINARY HASH.  Even if the version string matches (go1.25.11),
# a downloaded tarball may produce a different compiler hash than the binary
# used inside the Docker image.
# The only guaranteed fix: rebuild adaptixserver with OUR go binary, then
# build the plugins with the same go binary → hashes are identical by
# construction.
AXC2_CLONE=/tmp/adaptixc2-src
if [ ! -d "$AXC2_CLONE" ]; then
    echo "Cloning TGJLS/AdaptixC2 for server rebuild..."
    git clone --depth=1 https://github.com/TGJLS/AdaptixC2 "$AXC2_CLONE"
else
    echo "Using existing TGJLS/AdaptixC2 clone at ${AXC2_CLONE}"
fi

echo "Rebuilding /app/adaptixserver from ${AXC2_CLONE}/AdaptixServer ..."
(
    cd "${AXC2_CLONE}/AdaptixServer"
    # ensure all module deps are present (runs offline if already cached)
    go mod download 2>/dev/null || true
    GOEXPERIMENT="${BINARY_GOEXP}" CGO_ENABLED=1 \
        go build -ldflags="-s -w" -o /app/adaptixserver .
    echo "Rebuilt: $(go version -m /app/adaptixserver 2>/dev/null | head -1)"
)

# --- Inspect adaptixserver binary ---
# Print the full dependency list so CI logs tell us exactly what axc2 version
# (and any replace directives) the running server was compiled with.
echo "=== adaptixserver build info ==="
go version -m /app/adaptixserver 2>/dev/null || echo "(go version -m failed)"

# Extract the exact axc2 version embedded in the binary.  If the server used a
# local/replace source the output is "(devel)" — fall back to v1.2.0 in that case.
BINARY_AXC2=$(go version -m /app/adaptixserver 2>/dev/null | \
    awk '/github\.com\/Adaptix-Framework\/axc2/{print $3}') || BINARY_AXC2=""
echo "axc2 in binary: ${BINARY_AXC2:-unknown}"
if [[ "${BINARY_AXC2}" =~ ^v[0-9] ]]; then
    AXC2_VERSION="${BINARY_AXC2}"
else
    AXC2_VERSION=v1.2.0
fi
echo "Pinning axc2 to ${AXC2_VERSION}"

# --- Build combined go.work FIRST ---
# Both listener and agent plugins must be built with a single go.work so that
# all shared packages (especially axc2) resolve to the exact same versions as
# the running adaptixserver binary. Building without this causes:
#   plugin.Open: "plugin was built with a different version of package axc2"
# We discover ALL go.mod files in the TGJLS/AdaptixC2 clone dynamically so
# that any local axc2 module (pointed to by a replace directive) is included.
ADAPTIX_SRC="$AXC2_CLONE"

echo "=== Modules found in AdaptixC2 clone (diagnostic only) ==="
while IFS= read -r gomod_path; do
    dir=$(dirname "$gomod_path")
    echo "  ${dir}: $(head -1 "$gomod_path")"
    if grep -q 'Adaptix-Framework/axc2' "$gomod_path" 2>/dev/null; then
        grep 'Adaptix-Framework/axc2' "$gomod_path" | sed 's/^/    axc2 in go.mod: /'
    fi
done < <(find "$ADAPTIX_SRC" -name "go.mod" -not -path "*/vendor/*" | sort)

# Build a go.work with ONLY the Kharon modules so that MVS picks exactly the
# axc2 version we pinned via "go get", without interference from AdaptixC2's
# potentially-newer go.mod (the docker image may lag behind the repo HEAD).
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

# --- Build Kharon listener ---
echo "Building Kharon listener..."
echo "=== listener_kharon_http/Makefile ==="
cat "${KHARON_DIR}/listener_kharon_http/Makefile" 2>/dev/null || echo "(no Makefile)"
cd "${KHARON_DIR}/listener_kharon_http"
go get "github.com/Adaptix-Framework/axc2@${AXC2_VERSION}"
GOWORK="${COMBINED_WORK}" GOEXPERIMENT="${BINARY_GOEXP}" make all

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
rm -f dist/agent_kharon.so
cd "${KHARON_DIR}/agent_kharon/src_server"
GOWORK="${COMBINED_WORK}" GOEXPERIMENT="${BINARY_GOEXP}" \
    go build -buildmode=plugin -o "../dist/agent_kharon.so" .
echo "Built: $(ls -sh ../dist/agent_kharon.so)"

echo "=== axc2 version in built plugins ==="
go version -m "${KHARON_DIR}/listener_kharon_http/dist/listener_kharon_http.so" 2>/dev/null | \
    awk '/axc2/{print "  listener: "$0}' || echo "  listener: (read failed)"
go version -m "${KHARON_DIR}/agent_kharon/dist/agent_kharon.so" 2>/dev/null | \
    awk '/axc2/{print "  agent:    "$0}' || echo "  agent: (read failed)"
echo "  server:   $(go version -m /app/adaptixserver 2>/dev/null | awk '/axc2/{print $0}')"

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
