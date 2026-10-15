#!/usr/bin/env bash
# Start the SOWSprint.ai stack.
#
#   ./scripts/start.sh              start and wait until the app answers
#   ./scripts/start.sh --build      rebuild the image first (after changing src/)
#   ./scripts/start.sh --no-wait    return as soon as the containers are created
#
# Deliberately not automatic. The containers are created with `restart: "no"`, so a reboot
# leaves them stopped and this is the one command that brings them back. That matters on a
# shared machine: the stack holds 8000, 6333 and 6334, and a project that starts itself is a
# project that breaks another one at three in the morning.
#
# Safe to run twice. If the stack is already up it says so and does nothing.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

BUILD=0
WAIT=1
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=1 ;;
    --no-wait) WAIT=0 ;;
    -h|--help) sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) bad "unknown option: $arg"; exit 2 ;;
  esac
done

head_ "Starting SOWSprint.ai"
require_docker

if [ ! -f docker-compose.yml ]; then
  bad "docker-compose.yml not found — run this from the project directory."
  exit 1
fi

if [ ! -f .env ]; then
  warn ".env is missing; the app will start with defaults and no API keys."
  info "copy .env.example to .env and fill it in if you need live integrations."
fi

# Already running?
running="$(docker ps --filter 'name=sowsprint-app' --format '{{.Names}}' 2>/dev/null || true)"
if [ -n "$running" ] && [ "$BUILD" -eq 0 ]; then
  ok "already running"
  project_summary
  exit 0
fi

# Ports are checked before anything is created, so a conflict costs nothing to resolve —
# rather than a half-started stack and a bind error in the logs.
head_ "Checking ports"
if ! check_ports_available; then
  say ""
  bad "Refusing to start: another project holds a port this stack needs."
  say "     Stop that project first, or change SOWSPRINT_HOST_PORT in .env."
  say "     To see what is running:  ./scripts/status.sh"
  exit 1
fi
ok "ports $ALL_PORTS are free"

if [ "$BUILD" -eq 1 ]; then
  head_ "Building the image"
  info "src/ is copied into the image, so code changes need a rebuild."
  compose build
fi

head_ "Starting services"
compose up -d

if [ "$WAIT" -eq 1 ]; then
  head_ "Waiting for the application"
  wait_for_health 240 || true
fi

project_summary
