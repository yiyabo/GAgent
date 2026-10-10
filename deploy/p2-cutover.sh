#!/usr/bin/env bash
# P2 cutover: switch production from the ad-hoc `phage-agent` container to the
# containerized stack (LOCAL_INFRA §121). Run this ON .8, from this directory.
#
#   ./p2-cutover.sh            # prints what it would do; changes nothing
#   ./p2-cutover.sh --yes      # performs the cutover
#   ./p2-cutover.sh --rollback # switches back to the old container
#
# Order and rationale (each step is here for a measured reason — see §121):
#   1. gate         refuse if a chat run or plan job is in flight
#   2. stop old     frees host port 40003; the container is NOT deleted, which is
#                   what makes --rollback a one-liner
#   3. rsync delta  validated by dry run to be ~2 files / 259 KB (sub-second).
#                   Runs AFTER the stop so the copy is consistent.
#   4. chown        the volume and the runtime tree must be app-owned (uid 1001);
#                   Docker copies image ownership into a fresh volume, but the
#                   seed/delta arrive as root.
#   5. up           stack on the production port, with the prebuilt images
#   6. smoke        import + readiness + zero tracebacks
#   7. reprobe      the auth boundary from outside (must be 401 / refused)
#
# A bulk pre-pass (volume seed + first chown) is expected to have run earlier so
# this window only pays the delta.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${HERE}/.." && pwd)"
COMPOSE=(docker compose -f "${HERE}/docker-compose.yml")
OLD_CONTAINER="phage-agent"
DB_SRC="/data/phage-agent/data/databases/"
DB_VOL_MOUNT="/data/docker/volumes/gagent-data/_data/"
# The volume mirrors the host tree, so the DBs live under a `databases/`
# subdirectory — the stack sets DB_ROOT=/app/data/databases. Syncing into the
# volume ROOT instead would leave the app looking one level above its data and
# starting on an empty database.
DB_VOL_DB_DIR="${DB_VOL_MOUNT}databases/"
RUNTIME_DIR="/data/phage-agent/runtime"
APP_UID_GID="1001:1001"
AUDIT_LOG="/data/logs/restarts.log"
STAMP="$(date +%Y%m%d-%H%M%S)"
LOG="/tmp/p2-cutover-${STAMP}.log"

log() { printf '[%s] %s\n' "$(date +%H:%M:%S)" "$*" | tee -a "${LOG}"; }
die() { log "ERROR: $*"; exit 1; }

# NOTE: config is loaded AFTER the dry-run branch below, so `./p2-cutover.sh`
# with no arguments prints the plan on a machine that has no .env yet.

gate() {
  log "gate: active chat runs + queued/running plan jobs"
  docker exec -w /app "${OLD_CONTAINER}" python3 - <<'PY' | tee -a "${LOG}"
import glob, sqlite3
c = sqlite3.connect("file:/app/data/databases/main/plan_registry.db?mode=ro", uri=True)
runs = c.execute("SELECT COUNT(*) FROM chat_runs WHERE status IN ('running','queued')").fetchone()[0]
jobs = 0
for p in glob.glob("/app/data/databases/plans/*.sqlite"):
    try:
        jobs += sqlite3.connect("file:" + p + "?mode=ro", uri=True).execute(
            "SELECT COUNT(*) FROM decomposition_jobs WHERE status IN ('queued','running')").fetchone()[0]
    except Exception:
        pass
print(f"gate: active_chat_runs={runs} active_plan_jobs={jobs}")
raise SystemExit(1 if (runs or jobs) else 0)
PY
}

smoke() {
  log "smoke: import + readiness + tracebacks"
  "${COMPOSE[@]}" exec -T app python -c "import app.main; print('IMPORT_OK')" >>"${LOG}" 2>&1 || die "import failed"
  local code
  for _ in $(seq 1 24); do
    code=$(curl -s -o /dev/null -w '%{http_code}' -m 8 "http://127.0.0.1:${PORT}/health/ready")
    [ "${code}" = "200" ] && break
    sleep 5
  done
  [ "${code}" = "200" ] || die "gateway /health/ready returned ${code}"
  local tb
  tb=$("${COMPOSE[@]}" logs --since 4m app 2>&1 | grep -ci traceback || true)
  [ "${tb}" = "0" ] || die "found ${tb} traceback(s) in app logs"
  log "smoke OK (ready=200, tracebacks=0)"
}

reprobe() {
  log "re-probe: protected paths must be 401 and the anonymous WS refused"
  local rc=0
  for p in /tools/available /usage/overview /tasks/1/result /plans/1/results /project/1/files; do
    local code; code=$(curl -s -o /dev/null -w '%{http_code}' -m 12 "http://127.0.0.1:${PORT}${p}")
    printf '  %-24s -> %s\n' "${p}" "${code}" | tee -a "${LOG}"
    [ "${code}" = "401" ] || rc=1
  done
  local ws; ws=$(curl -s -i -N -m 8 -H "Connection: Upgrade" -H "Upgrade: websocket" \
    -H "Sec-WebSocket-Version: 13" -H "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==" \
    "http://127.0.0.1:${PORT}/ws/terminal/probe" 2>&1 | grep -cE "^HTTP/1.1 (403|401)" || true)
  [ "${ws}" -ge 1 ] || rc=1
  [ "${rc}" -eq 0 ] || die "re-probe failed: an endpoint that must be 401 is not"
  log "re-probe OK"
}

rollback() {
  log "ROLLBACK: stopping the stack and restarting ${OLD_CONTAINER}"
  "${COMPOSE[@]}" down >>"${LOG}" 2>&1
  docker start "${OLD_CONTAINER}" >>"${LOG}" 2>&1 || die "could not restart ${OLD_CONTAINER}"
  sleep 10
  curl -s -o /dev/null -w "old container /health -> %{http_code}\n" -m 8 "http://127.0.0.1:40003/health" | tee -a "${LOG}"
  log "rollback done; production is back on the old container"
}

case "${1:-}" in
  --rollback) rollback; exit 0 ;;
  --yes) ;;
  "") echo "DRY RUN — no changes. Steps:"; sed -n '5,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; echo; echo "re-run with --yes to execute."; exit 0 ;;
  *) die "unknown argument: $1" ;;
esac

[ -f "${HERE}/.env" ] || die "missing ${HERE}/.env"
# shellcheck disable=SC1091
set -a; source "${HERE}/.env"; set +a
# .env is the *container* contract, not a host-shell contract: it carries
# DOCKER_HOST=tcp://proxy:2375, and `proxy` is a compose service name the host
# CLI cannot resolve. Every docker invocation in this script runs on the HOST
# (gate/smoke/reprobe/stop/up), so drop it — otherwise the gate dies with
# "lookup proxy: Temporary failure in name resolution" before it ever queries
# the database, and the cutover aborts for a reason that is not the gate.
# `docker compose` reads .env itself for the app service's environment, so
# unsetting it here does not change the stack's config.
unset DOCKER_HOST
PORT="${GATEWAY_PORT:-40003}"

log "=== P2 cutover ${STAMP} (port ${PORT}) ==="
gate || die "gate not clear — nothing done"
log "step 1/6 stop old container"
docker stop "${OLD_CONTAINER}" >>"${LOG}" 2>&1 || die "could not stop ${OLD_CONTAINER}"
log "step 2/6 rsync delta into the volume"
[ -d "${DB_VOL_DB_DIR}" ] || die "volume layout wrong: ${DB_VOL_DB_DIR} missing (expected a databases/ subdir)"
rsync -a --delete "${DB_SRC}" "${DB_VOL_DB_DIR}" >>"${LOG}" 2>&1 || die "rsync delta failed"
log "step 3/6 chown volume + runtime tree to ${APP_UID_GID}"
chown -R "${APP_UID_GID}" "${DB_VOL_MOUNT}" >>"${LOG}" 2>&1 || die "volume chown failed"
chown -R "${APP_UID_GID}" "${RUNTIME_DIR}" >>"${LOG}" 2>&1 || die "runtime chown failed"
log "step 4/6 bring the stack up"
"${COMPOSE[@]}" up -d --no-build >>"${LOG}" 2>&1 || die "compose up failed"
log "step 5/6 smoke"
smoke
log "step 6/6 re-probe"
reprobe
printf '%s caller=p2-cutover port=%s result=cutover\n' "$(date -Iseconds)" "${PORT}" >>"${AUDIT_LOG}"
log "=== P2 cutover COMPLETE ==="
log "rollback if needed:  ${HERE}/p2-cutover.sh --rollback"
log "full log: ${LOG}"
