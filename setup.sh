#!/usr/bin/env bash
set -euo pipefail

CMD="${1:-}"

case "$CMD" in
  init)
    # ── KVM check ──────────────────────────────────────────────────────────────
    if [ -e /dev/kvm ]; then
      echo "✓ /dev/kvm found — Windows will boot at full speed"
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

    # ── SSH keypair ─────────────────────────────────────────────────────────────
    mkdir -p ci/ssh
    if [ -f ci/ssh/id_test ]; then
      echo "⚠  ci/ssh/id_test already exists — delete it first to regenerate"
    else
      ssh-keygen -t ed25519 -N "" -f ci/ssh/id_test -C "ci-testing-kit"
      chmod 600 ci/ssh/id_test
      echo "✓ SSH keypair generated at ci/ssh/id_test"
    fi

    # ── Render unattend.xml ─────────────────────────────────────────────────────
    PUBLIC_KEY=$(cat ci/ssh/id_test.pub)
    sed "s|{{PUBLIC_KEY}}|${PUBLIC_KEY}|g" \
      ci/windows/unattend.xml.template > ci/windows/unattend.xml
    echo "✓ ci/windows/unattend.xml rendered"

    # ── Generate config.yaml ────────────────────────────────────────────────────
    cat > ci/config.yaml <<'CONFIG'
server:
  url: https://127.0.0.1:4321
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
        - "10.0.2.2:8080"
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
  host: 127.0.0.1
  port: 2222
  username: ci_runner
  key_path: /run/secrets/ssh_key
  source_path: /tmp/ci_agent.exe
  agent_path: 'C:\ci\agent.exe'
  terminate: true
  connect_retries: 30
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
    docker compose up -d windows adaptixc2
    ;;

  test)
    docker compose run --rm testing-kit
    ;;

  down)
    docker compose down -v
    ;;

  *)
    echo "Usage: $0 {init|up|test|down}"
    exit 1
    ;;
esac
