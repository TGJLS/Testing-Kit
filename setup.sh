#!/usr/bin/env bash
set -euo pipefail

CMD="${1:-}"

case "$CMD" in
  init)
    # ── KVM check ──────────────────────────────────────────────────────────────
    # docker-compose.override.yml adds /dev/kvm device to the windows service.
    # Compose merges it automatically alongside docker-compose.yml.
    if [ -e /dev/kvm ]; then
      echo "✓ /dev/kvm found — writing docker-compose.override.yml to enable KVM acceleration"
      cat > docker-compose.override.yml <<'OVERRIDE'
services:
  windows:
    devices:
      - /dev/kvm:/dev/kvm
OVERRIDE
    else
      echo "⚠  /dev/kvm not available — Windows will use software emulation (slow)"
      rm -f docker-compose.override.yml
    fi

    # ── btrfs copy-on-write check ────────────────────────────────────────────────
    # QEMU's raw disk image does lots of small synchronous random writes during
    # Windows Setup. btrfs's default copy-on-write causes heavy fragmentation and
    # write-amplification for that pattern, making the install dramatically
    # slower (I/O-wait bound regardless of free RAM/CPU). chattr +C must be set
    # on the volume directory *before* the image file is created — it does not
    # apply retroactively to existing data, so this must run on a fresh/empty
    # volume (e.g. right after './setup.sh reset').
    #
    # The volume dir under /var/lib/docker is root-owned, so this runs the
    # check/fix inside a throwaway container (CAP_LINUX_IMMUTABLE) instead of
    # requiring host sudo — the docker group already grants root-equivalent
    # access to the underlying files.
    DOCKER_ROOT="$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || echo /var/lib/docker)"
    if [ "$(stat -f -c %T "$DOCKER_ROOT" 2>/dev/null)" = "btrfs" ]; then
      docker volume create testing-kit_windows-data >/dev/null
      ATTR="$(docker run --rm --cap-add LINUX_IMMUTABLE -v testing-kit_windows-data:/data alpine sh -c \
        'apk add --no-cache e2fsprogs-extra >/dev/null 2>&1; lsattr -d /data' 2>/dev/null)"
      case "$ATTR" in
        *C*) echo "✓ btrfs detected — copy-on-write already disabled on testing-kit_windows-data volume" ;;
        *)
          docker run --rm --cap-add LINUX_IMMUTABLE -v testing-kit_windows-data:/data alpine sh -c \
            'apk add --no-cache e2fsprogs-extra >/dev/null 2>&1; chattr +C /data' >/dev/null 2>&1
          echo "✓ btrfs detected — disabled copy-on-write on testing-kit_windows-data volume"
          ;;
      esac
    fi

    # ── SSH keypair ─────────────────────────────────────────────────────────────
    mkdir -p ci/ssh ci/oem
    if [ -f ci/ssh/id_test ]; then
      echo "⚠  ci/ssh/id_test already exists — delete it first to regenerate"
    else
      ssh-keygen -t ed25519 -N "" -f ci/ssh/id_test -C "ci-testing-kit"
      chmod 600 ci/ssh/id_test
      echo "✓ SSH keypair generated at ci/ssh/id_test"
    fi

    # Download OpenSSH portable binary (used by install.bat — no internet needed in the VM)
    if [ ! -f ci/oem/OpenSSH-Win64.zip ]; then
      echo "Downloading OpenSSH-Win64.zip ..."
      curl -fsSL -o ci/oem/OpenSSH-Win64.zip \
        "https://github.com/PowerShell/Win32-OpenSSH/releases/latest/download/OpenSSH-Win64.zip"
      echo "✓ OpenSSH-Win64.zip downloaded"
    else
      echo "⚠  ci/oem/OpenSSH-Win64.zip already exists — skipping download"
    fi

    # Render install.bat with the public key embedded
    PUBLIC_KEY=$(cat ci/ssh/id_test.pub)
    sed "s|{{PUBLIC_KEY}}|${PUBLIC_KEY}|g" \
      ci/oem/install.bat.template > ci/oem/install.bat
    echo "✓ ci/oem/install.bat rendered"

    # ── Generate config.yaml ────────────────────────────────────────────────────
    # Addresses use Docker bridge DNS (service names) and the fixed adaptixc2 IP
    # (172.28.0.10) for the Windows beacon callback — reachable via QEMU SLIRP NAT.
    cat > ci/config.yaml <<'CONFIG'
server:
  url: https://adaptixc2:4321
  endpoint: /endpoint

operator:
  name: ci
  password: pass

setup:
  project: ci
  agent_output: /tmp/ci_agent.exe
  listener:
    name: ci_http
    type: BeaconHTTP
    config:
      host_bind: "0.0.0.0"
      port_bind: 8080
      callback_addresses:
        - "172.28.0.10:8080"
      http_method: POST
      uri:
        - /beacon
      user_agent:
        - "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
      hb_header: "X-Beacon-Id"
      encrypt_key: "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
      ssl: false
      page-payload: '{"status":"ok","data":"<<<PAYLOAD_DATA>>>","metrics":"sync"}'
  agent:
    agent: beacon
    listener: ci_http
    listener_type: BeaconHTTP
    config:
      arch: x64
      format: Exe
      sleep: "0s"
      jitter: 0

ssh:
  host: windows
  port: 22
  username: ci_runner
  key_path: /run/secrets/ssh_key
  source_path: /tmp/ci_agent.exe
  agent_path: 'C:\ci\agent.exe'
  terminate: true
  connect_retries: 120
  connect_retry_interval: 20
  preamble:
    - 'New-Item -ItemType Directory -Force -Path C:\ci | Out-Null'
    - 'Set-MpPreference -DisableRealtimeMonitoring $true'
    - 'Add-MpPreference -ExclusionPath C:\ci'
    - 'New-NetFirewallRule -DisplayName CI_C2_8080 -Direction Inbound -Protocol TCP -LocalPort 8080 -Action Allow -Profile Any | Out-Null'
CONFIG
    echo "✓ ci/config.yaml generated"
    ;;

  up)
    if [ ! -f ci/ssh/id_test ] || [ ! -f ci/oem/install.bat ] || [ ! -f ci/oem/OpenSSH-Win64.zip ] || [ ! -f ci/config.yaml ]; then
      echo "✗ Run './setup.sh init' first"
      exit 1
    fi
    docker compose up -d windows adaptixc2
    ;;

  test)
    docker compose run --rm testing-kit
    ;;

  down)
    docker compose down
    ;;

  reset)
    docker compose down -v
    ;;

  *)
    echo "Usage: $0 {init|up|test|down|reset}"
    exit 1
    ;;
esac
