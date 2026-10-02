#!/usr/bin/env bash
# The Postgres the cloud sessions test against: the same image, user,
# password and database CI's `postgres` service uses (.github/workflows/ci.yml),
# so a cloud pass predicts a CI pass. tests/test_dev_process_config.py parses
# both files and fails if they drift.
#
#   scripts/cloud-postgres.sh pull    # setup script: have the image on disk
#   scripts/cloud-postgres.sh start   # session start: run it, wait, and print
#                                     # `export PYRITE_TEST_PG_URL=...` on stdout
#                                     # ONLY when the database accepts connections
#
# Never fails: a session must not fail to start because Postgres did. Every
# problem is one line on stderr and no export. It is the pgvector image on
# purpose; the VM's pre-installed PostgreSQL has no pgvector extension, and
# PostgresBackend.ensure_schema() runs `CREATE EXTENSION vector`.
#
# PYRITE_SETUP_POSTGRES=0 opts out (no pull, no container, no export).
# Knobs for tests and odd machines: PYRITE_DOCKER (the docker binary),
# PYRITE_PG_PORT (host port, default 5432), PYRITE_PG_WAIT (seconds, default 60).
set -uo pipefail

IMAGE="pgvector/pgvector:pg16"
PG_USER="postgres"
PG_PASSWORD="postgres"
PG_DB="pyrite_test"
CONTAINER="pyrite-test-pg"

docker_bin="${PYRITE_DOCKER:-docker}"
port="${PYRITE_PG_PORT:-5432}"
wait_s="${PYRITE_PG_WAIT:-60}"

warn() { echo "pyrite: postgres tests off: $*" >&2; }
d() { "$docker_bin" "$@"; }

[ "${PYRITE_SETUP_POSTGRES:-1}" != "0" ] || exit 0

case "${1:-}" in
  pull)
    command -v "$docker_bin" >/dev/null 2>&1 || { warn "no docker on this machine"; exit 0; }
    # dockerd is installed but not running in a cloud VM; start it to pull.
    "$0" _daemon || exit 0
    d image inspect "$IMAGE" >/dev/null 2>&1 || d pull -q "$IMAGE" >/dev/null || warn "could not pull $IMAGE"
    ;;
  _daemon)
    command -v "$docker_bin" >/dev/null 2>&1 || exit 1
    if ! d info >/dev/null 2>&1; then
      if command -v dockerd >/dev/null 2>&1 && [ "$(id -u)" = 0 ]; then
        (dockerd >/tmp/pyrite-dockerd.log 2>&1 &)
      fi
      for _ in $(seq 1 30); do d info >/dev/null 2>&1 && break; sleep 1; done
    fi
    d info >/dev/null 2>&1 || { warn "docker daemon is not running"; exit 1; }
    ;;
  start)
    command -v "$docker_bin" >/dev/null 2>&1 || { warn "no docker on this machine"; exit 0; }
    "$0" _daemon || exit 0
    if [ -z "$(d ps -q -f "name=^${CONTAINER}\$" 2>/dev/null)" ]; then
      if [ -n "$(d ps -aq -f "name=^${CONTAINER}\$" 2>/dev/null)" ]; then
        d start "$CONTAINER" >/dev/null 2>&1 || { warn "could not start $CONTAINER"; exit 0; }
      else
        # An image not already on disk (no setup script ran) is pulled here, bounded.
        if ! d image inspect "$IMAGE" >/dev/null 2>&1; then
          if command -v timeout >/dev/null 2>&1; then timeout 240 "$docker_bin" pull -q "$IMAGE" >/dev/null 2>&1
          else d pull -q "$IMAGE" >/dev/null 2>&1; fi
        fi
        d run -d --name "$CONTAINER" \
          -e POSTGRES_PASSWORD="$PG_PASSWORD" -e POSTGRES_DB="$PG_DB" \
          -p "127.0.0.1:${port}:5432" "$IMAGE" >/dev/null 2>&1 \
          || { warn "could not run $IMAGE on port $port"; exit 0; }
      fi
    fi
    # `-h 127.0.0.1`: the official image's first-boot init runs a temporary
    # server on the unix socket only, and pg_isready over the socket says
    # "ready" before the real server (TCP) exists.
    ready=0
    for _ in $(seq 1 "$wait_s"); do
      if d exec "$CONTAINER" pg_isready -h 127.0.0.1 -U "$PG_USER" -d "$PG_DB" >/dev/null 2>&1 \
         && (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then ready=1; break; fi
      sleep 1
    done
    [ "$ready" = 1 ] || { warn "$CONTAINER did not accept connections within ${wait_s}s"; exit 0; }
    echo "export PYRITE_TEST_PG_URL=postgresql://${PG_USER}:${PG_PASSWORD}@localhost:${port}/${PG_DB}"
    ;;
  *)
    echo "usage: $0 pull|start" >&2
    ;;
esac
exit 0
