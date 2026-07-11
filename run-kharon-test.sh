#!/usr/bin/env bash
# Run the Kharon extender test locally — mirrors .github/workflows/test.yaml test-kharon job.
# Usage: ./run-kharon-test.sh [--no-build] [--no-teardown]
set -euo pipefail

cd "$(dirname "$0")"

BUILD=true
TEARDOWN=true
for arg in "$@"; do
  case "$arg" in
    --no-build)    BUILD=false ;;
    --no-teardown) TEARDOWN=false ;;
    *) echo "Unknown arg: $arg"; exit 1 ;;
  esac
done

cleanup() {
  echo ""
  echo "=== adaptixc2 logs (last 100 lines) ==="
  docker compose logs adaptixc2 2>/dev/null | tail -100 || true
  if $TEARDOWN; then
    echo "=== Tearing down ==="
    docker compose down || true
  fi
}
trap cleanup EXIT

if $BUILD; then
  echo "=== Building CLI ==="
  cd cli && go build -o ../testing-kit-cli .
  cd ..

  echo "=== Building Docker image ==="
  docker build -t ghcr.io/tgjls/testing-kit:2.1.0 .
fi

echo "=== Starting containers ==="
docker compose up -d

echo "=== Waiting for testing-kit API ==="
for i in $(seq 1 30); do
  if curl -sf http://localhost:1234/health > /dev/null 2>&1; then
    echo "✓ testing-kit API ready (attempt $i)"
    break
  fi
  echo "Waiting for testing-kit API (attempt $i/30)..."
  sleep 2
  [ "$i" -eq 30 ] && { echo "testing-kit API did not become ready in time"; exit 1; }
done

echo "=== Adding Kharon extender ==="
PROFILE_B64=$(base64 -w0 .github/cicd/kharon-malleable-profile.json)
./testing-kit-cli add-extender \
  --install-script .github/cicd/install-kharon.sh \
  --override "listener.port_bind=8080" \
  --override "listener.block_user_agents=" \
  --override "listener.domain_rotation_strategy=Random" \
  --override "listener.proxy_url=" \
  --override "listener.proxy_user=" \
  --override "listener.proxy_pass=" \
  --override "listener.ssl_cert=" \
  --override "listener.ssl_key=" \
  --override "listener.uploaded_file=${PROFILE_B64}" \
  https://github.com/entropy-z/Kharon

echo "=== Seeding Kharon tasks ==="
curl -sf -X PUT http://localhost:1234/v1/tasks/batch \
  -H "Content-Type: application/json" \
  -d "$(python3 -c "
import json, yaml
data = yaml.safe_load(open('.github/cicd/kharon-tasks.yaml'))
print(json.dumps(data['tasks']))
")"
echo ""

echo "=== Waiting for Windows SSH (up to 50 min) ==="
for i in $(seq 1 150); do
  if docker compose exec testing-kit python3 -c \
      "import socket,sys; socket.create_connection(('windows',22),5).close()" \
      2>/dev/null; then
    echo "✓ Windows SSH ready (attempt $i)"
    break
  fi
  echo "Waiting for Windows SSH (attempt $i/150)..."
  sleep 20
  [ "$i" -eq 150 ] && {
    docker compose logs windows | tail -50
    echo "Windows SSH not ready after 50 minutes"
    exit 1
  }
done

echo "=== Running tests ==="
./testing-kit-cli run-tests
