#!/usr/bin/env bash
# Shared helpers for the start / stop / status scripts.
#
# Sourced, never executed. Kept in one place because the alternative — the same port check
# copied into three scripts — is three chances for them to disagree about what "running"
# means, and the port check is the part that matters when several projects share a machine.

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_ROOT"

# ---------------------------------------------------------------------------- output
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'
else
  C_RESET=""; C_BOLD=""; C_DIM=""; C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""
fi

say()  { printf '%s\n' "$*"; }
head_() { printf '\n%s%s%s\n' "$C_BOLD" "$*" "$C_RESET"; }
ok()   { printf '  %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '  %s!%s %s\n' "$C_YELLOW" "$C_RESET" "$*"; }
bad()  { printf '  %s✗%s %s\n' "$C_RED" "$C_RESET" "$*"; }
info() { printf '  %s·%s %s\n' "$C_DIM" "$C_RESET" "$*"; }

# ---------------------------------------------------------------------------- compose
# `docker compose` (v2, a plugin) is what this project targets; `docker-compose` (v1
# standalone) is accepted so an older laptop still works rather than failing on a
# technicality that has nothing to do with the project.
compose() {
  if docker compose version >/dev/null 2>&1; then
    docker compose "$@"
  elif command -v docker-compose >/dev/null 2>&1; then
    docker-compose "$@"
  else
    bad "Neither 'docker compose' nor 'docker-compose' is available."
    say "     Install Docker Engine with the Compose plugin, or Docker Desktop."
    exit 1
  fi
}

require_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    bad "The 'docker' command was not found."
    say "     Linux:  https://docs.docker.com/engine/install/"
    say "     Windows: install Docker Desktop and enable WSL 2 integration."
    exit 1
  fi
  if ! docker info >/dev/null 2>&1; then
    bad "Docker is installed but not responding."
    say "     Linux:  sudo systemctl start docker"
    say "     Windows: start Docker Desktop and wait for the whale to settle."
    exit 1
  fi
}

# The published host ports, read from .env the same way compose reads it. Defaults match
# docker-compose.yml, so a missing .env is not fatal here.
host_port() {
  local key="$1" fallback="$2" value=""
  if [ -f .env ]; then
    value="$(grep -E "^${key}=" .env 2>/dev/null | tail -1 | cut -d= -f2- | tr -d '"'"'"' \r' || true)"
  fi
  printf '%s' "${value:-$fallback}"
}

APP_PORT="$(host_port SOWSPRINT_HOST_PORT 8000)"
QDRANT_HTTP_PORT="$(host_port SOWSPRINT_QDRANT_HTTP_PORT 6333)"
QDRANT_GRPC_PORT="$(host_port SOWSPRINT_QDRANT_GRPC_PORT 6334)"
ALL_PORTS="$APP_PORT $QDRANT_HTTP_PORT $QDRANT_GRPC_PORT"

# ---------------------------------------------------------------------------- ports
# Who holds this port, and is it us?
#
# The whole point is that other projects share the machine. A bare bind failure ("address
# already in use") sends the operator hunting through `docker ps`; naming the container and
# the compose project that holds the port turns a confusing failure into an instruction.
port_holder() {
  local port="$1"
  # Match the host side of every published mapping, including ranges.
  #
  # A naive `index($3, ":6333->")` misses Qdrant entirely: compose publishes `6333-6334`, so
  # the field reads `0.0.0.0:6333-6334->6333-6334/tcp` and a single-port pattern never
  # matches. The script then reported the port as "held outside Docker" while the container
  # holding it was listed two lines above — the kind of wrong answer that costs an hour.
  docker ps --format '{{.Names}}\t{{.Label "com.docker.compose.project"}}\t{{.Ports}}' 2>/dev/null \
    | awk -F'\t' -v want="$port" '
        {
          n = split($3, maps, ", ")
          for (i = 1; i <= n; i++) {
            # Only mappings published on the host have an "->". A bare "8000/tcp" is a port
            # the container EXPOSES to the compose network and is not bound on the host at
            # all — treating it as taken made the script refuse to start over a container
            # that was not in the way. `alpha_beat` exposed 8000 this way and blocked the
            # stack while `ss` showed nothing listening.
            if (index(maps[i], "->") == 0) continue
            split(maps[i], arrow, "->")
            host = arrow[1]
            sub(/.*:/, "", host)               # strip the bind address
            if (host ~ /^[0-9]+-[0-9]+$/) {    # a range, e.g. 6333-6334
              split(host, r, "-")
              if (want + 0 >= r[1] + 0 && want + 0 <= r[2] + 0) { print $1 "\t" $2; exit }
            } else if (host + 0 == want + 0) {
              print $1 "\t" $2; exit
            }
          }
        }'
}

port_is_free() {
  local port="$1"
  [ -z "$(port_holder "$port")" ] || return 1
  # Something outside Docker may hold it too: a local Postgres, an SSH tunnel, another
  # tool entirely.
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$" && return 1
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -qE "[:.]${port}$" && return 1
  fi
  return 0
}

# Refuse to start over another project's port. Returns non-zero when it is unsafe.
check_ports_available() {
  local conflicts=0 dev_conflict=0 other_conflict=0
  for port in $ALL_PORTS; do
    if ! port_is_free "$port"; then
      local holder project
      holder="$(port_holder "$port" | cut -f1)"
      project="$(port_holder "$port" | cut -f2)"
      if [ -n "$holder" ]; then
        if [ "$project" = "sowsprint" ]; then
          info "port $port is held by this project ($holder)"
        else
          bad "port $port is held by container '$holder' of project '$project'"
          # The dev container is this same application, not a competitor. Saying "another
          # project" would send the operator looking for a stranger when the answer is the
          # window they already have open.
          case "$project" in
            sowsprint-dev)
              dev_conflict=1
              ;;
            *)
              other_conflict=1
              ;;
          esac
          conflicts=$((conflicts + 1))
        fi
      else
        bad "port $port is held by something outside Docker"
        if command -v ss >/dev/null 2>&1; then
          ss -ltnp 2>/dev/null | grep -E "[:.]${port}\b" | head -1 | sed 's/^/       /' || true
        fi
        conflicts=$((conflicts + 1))
      fi
    fi
  done
  if [ "$dev_conflict" -eq 1 ]; then
    say ""
    info "the dev container is running — that is the VS Code window, not another project"
    info "close it from VS Code:  Dev Containers: Close Remote Connection"
    info "or from here:           docker compose -f .devcontainer/docker-compose.yml down"
  fi
  if [ "$other_conflict" -eq 1 ]; then
    say ""
    info "another project holds a port — see ./scripts/status.sh for the full picture"
  fi
  [ "$conflicts" -eq 0 ]
}

# ---------------------------------------------------------------------------- health
wait_for_health() {
  local timeout="${1:-180}" waited=0 status
  while [ "$waited" -lt "$timeout" ]; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
      sowsprint-app 2>/dev/null || echo missing)"
    case "$status" in
      healthy) ok "application is healthy"; return 0 ;;
      missing) bad "container sowsprint-app is not present"; return 1 ;;
      unhealthy) bad "application reported unhealthy"; return 1 ;;
    esac
    sleep 5; waited=$((waited + 5))
    if [ $((waited % 30)) -eq 0 ]; then info "waiting for the application… ${waited}s"; fi
  done
  warn "still '$status' after ${timeout}s — check: docker compose logs sowsprint-app"
  return 1
}

project_summary() {
  head_ "SOWSprint.ai"
  printf '  %sWeb interface%s   http://localhost:%s\n' "$C_BLUE" "$C_RESET" "$APP_PORT"
  printf '  %sQdrant%s          http://localhost:%s  (dashboard)\n' "$C_BLUE" "$C_RESET" "$QDRANT_HTTP_PORT"
  printf '  %sLogs%s            docker compose logs -f sowsprint-app\n' "$C_DIM" "$C_RESET"
  if [ -f .env ]; then
    local login
    login="$(grep -E '^SOWSPRINT_AUTH_PASSWORD=' .env 2>/dev/null | tail -1 | cut -d= -f2- || true)"
    if [ -n "$login" ]; then
      printf '  %sLogin%s           see SOWSPRINT_AUTH_USERNAME / SOWSPRINT_AUTH_PASSWORD in .env\n' "$C_DIM" "$C_RESET"
    fi
  fi
}
