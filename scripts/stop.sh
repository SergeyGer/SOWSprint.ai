#!/usr/bin/env bash
# Stop the SOWSprint.ai stack.
#
#   ./scripts/stop.sh              stop and remove this project's containers
#   ./scripts/stop.sh --keep       stop without removing (starts faster next time)
#   ./scripts/stop.sh --all        also stop other containers holding our ports
#
# Only this project's containers are touched. `docker compose down` is scoped to the compose
# project in docker-compose.yml (`name: sowsprint`), so another project's services are not
# affected — which is the point when several of them share a laptop.
#
# Named volumes are kept. Stopping is not deleting: the Qdrant index and the generated
# artefacts survive, so the next start is seconds rather than a full re-ingest. Use
# `docker compose down -v` deliberately if you really want the index gone.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

KEEP=0
ALSO_OTHERS=0
for arg in "$@"; do
  case "$arg" in
    --keep) KEEP=1 ;;
    --all) ALSO_OTHERS=1 ;;
    -h|--help) sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) bad "unknown option: $arg"; exit 2 ;;
  esac
done

head_ "Stopping SOWSprint.ai"
require_docker

# What of ours is actually up? An empty answer is a success, not an error.
ours="$(docker ps --filter 'label=com.docker.compose.project=sowsprint' --format '{{.Names}}' 2>/dev/null || true)"
if [ -z "$ours" ]; then
  ok "nothing of this project is running"
else
  printf '  stopping: %s\n' "$(echo "$ours" | tr '\n' ' ')"
  if [ "$KEEP" -eq 1 ]; then
    compose stop
  else
    compose down --remove-orphans
  fi
  ok "stopped"
fi

# Anything else still holding the ports. Reported, never stopped without --all: a container
# belonging to another project is that project's business.
leftovers=""
for port in $ALL_PORTS; do
  holder="$(port_holder "$port" | cut -f1)"
  [ -n "$holder" ] && leftovers="$leftovers $port:$holder"
done

if [ -n "$leftovers" ]; then
  say ""
  warn "these ports are still held by other containers:"
  for item in $leftovers; do
    printf '       port %-22s %s\n' "${item%%:*}" "${item##*:}"
  done
  if [ "$ALSO_OTHERS" -eq 1 ]; then
    say ""
    for item in $leftovers; do
      name="${item##*:}"
      info "stopping $name"
      docker stop "$name" >/dev/null 2>&1 || warn "could not stop $name"
    done
    ok "done"
  else
    info "leave them alone with:  ./scripts/stop.sh    (they belong to other projects)"
    info "or stop them too with:  ./scripts/stop.sh --all"
  fi
else
  ok "ports $ALL_PORTS are free"
fi

say ""
info "the Qdrant index and generated artefacts were kept"
info "to remove them as well:  docker compose down -v"
