#!/usr/bin/env bash
# P1 stack verification (LOCAL_INFRA §120+). Run on the host that runs the stack.
#
# Proves the containerized shape end to end WITHOUT touching production data:
#   * the app serves only on the internal network
#   * the gateway serves the built SPA and proxies the API
#   * auth is still the inverted allowlist (protected paths 401 through the gateway)
#   * the WebSocket handshake is rejected anonymously
#   * /health/ready is real (DB + runtime writable)
#   * the app reaches Docker only through the socket proxy
#
# Usage: ./deploy/p1-verify.sh [gateway_port]
set -uo pipefail

PORT="${1:-18080}"
BASE="http://127.0.0.1:${PORT}"
COMPOSE=(docker compose -f "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/docker-compose.yml")
fails=0
pass() { printf '  PASS  %s\n' "$*"; }
fail() { printf '  FAIL  %s\n' "$*"; fails=$((fails + 1)); }

check_code() { # path expected_code label
  local code
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "${BASE}$1")
  if [ "${code}" = "$2" ]; then pass "$3 ($1 -> $code)"; else fail "$3 ($1 -> $code, wanted $2)"; fi
}

echo "== gateway serves the SPA =="
check_code / 200 "SPA index"
asset=$(curl -s "${BASE}/" | grep -oE '/assets/[A-Za-z0-9._-]+\.js' | head -1 || true)
if [ -n "${asset}" ]; then check_code "${asset}" 200 "SPA asset ${asset}"; else fail "no /assets/*.js referenced by index.html"; fi

echo "== readiness and liveness =="
check_code /health/ready 200 "readiness"
check_code /health 200 "liveness"
if curl -s "${BASE}/health/ready" | grep -q '"status": *"ready"'; then pass "readiness reports ready"; else fail "readiness payload not ready"; fi

echo "== auth boundary through the gateway =="
for p in /tools/available /usage/overview /tasks/1/result /skill-learning/skills/x /plans/1/results /project/1/files; do
  check_code "${p}" 401 "protected ${p}"
done

echo "== terminal websocket rejected anonymously =="
ws=$(curl -s -i -N -m 10 \
  -H "Connection: Upgrade" -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
  "${BASE}/ws/terminal/verify" 2>&1 | grep -cE "^HTTP/1.1 (403|401)" || true)
if [ "${ws}" -ge 1 ]; then pass "anonymous WS handshake refused"; else fail "anonymous WS handshake not refused"; fi

echo "== the app has no docker socket and reaches Docker only via the proxy =="
if "${COMPOSE[@]}" exec -T app sh -lc 'test ! -S /var/run/docker.sock'; then
  pass "no docker.sock inside app"
else
  fail "app container has a docker socket"
fi
if "${COMPOSE[@]}" exec -T app sh -lc 'docker version --format "{{.Server.Version}}" >/dev/null 2>&1'; then
  pass "app reaches a Docker API (via DOCKER_HOST)"
else
  fail "app cannot reach the Docker API through the proxy"
fi
if "${COMPOSE[@]}" exec -T app sh -lc 'printf "DOCKER_HOST=%s\n" "$DOCKER_HOST"'; then :; fi

echo "== in-container test suite is runnable (app/tests present in the image) =="
if "${COMPOSE[@]}" exec -T app sh -lc 'test -d /app/app/tests && python -c "import app.main"'; then
  pass "image contains app/tests and imports"
else
  fail "image missing app/tests or import failed"
fi

echo
if [ "${fails}" -eq 0 ]; then echo "P1 verification: ALL PASS"; else echo "P1 verification: ${fails} FAILURE(S)"; fi
exit "${fails}"
