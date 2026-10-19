#!/usr/bin/env bash
# Show what is running, and who holds the ports this project needs.
#
#   ./scripts/status.sh
#
# This is the script to reach for when something will not start. On a machine shared with
# other projects, "port already in use" is almost always another project's container, and the
# answer is a name, not a stack trace.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

require_docker

head_ "SOWSprint.ai"
ours="$(docker ps -a --filter 'label=com.docker.compose.project=sowsprint' \
        --format '{{.Names}}\t{{.Status}}\t{{.Ports}}' 2>/dev/null || true)"
if [ -z "$ours" ]; then
  info "no containers exist yet — start with ./scripts/start.sh"
else
  while IFS=$'\t' read -r name status ports; do
    [ -z "$name" ] && continue
    case "$status" in
      Up*) printf '  %s✓%s %-18s %s\n' "$C_GREEN" "$C_RESET" "$name" "$status" ;;
      *)   printf '  %s·%s %-18s %s\n' "$C_DIM" "$C_RESET" "$name" "$status" ;;
    esac
  done <<< "$ours"
fi

head_ "Ports"
for port in $ALL_PORTS; do
  holder="$(port_holder "$port" | cut -f1)"
  project="$(port_holder "$port" | cut -f2)"
  if [ -z "$holder" ]; then
    if port_is_free "$port"; then
      printf '  %s✓%s %-6s free\n' "$C_GREEN" "$C_RESET" "$port"
    else
      printf '  %s!%s %-6s held outside Docker\n' "$C_YELLOW" "$C_RESET" "$port"
    fi
  elif [ "$project" = "sowsprint" ]; then
    printf '  %s✓%s %-6s %s  %s(this project)%s\n' "$C_GREEN" "$C_RESET" "$port" "$holder" "$C_DIM" "$C_RESET"
  else
    printf '  %s✗%s %-6s %s  %s(project: %s)%s\n' "$C_RED" "$C_RESET" "$port" "$holder" "$C_YELLOW" "$project" "$C_RESET"
  fi
done

# Every other compose project on the machine, so switching between them is a decision rather
# than a discovery.
others="$(docker ps --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null \
          | grep -v '^$' | grep -v '^sowsprint$' | sort -u || true)"
if [ -n "$others" ]; then
  head_ "Other projects running on this machine"
  for project in $others; do
    count="$(docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' | wc -l | tr -d ' ')"
    # A dev container on a shared server is named `sowsprint-dev-<person>`. Saying "another
    # project" would send the operator looking for a stranger when the answer is a colleague
    # who left a VS Code window open — or themselves, from another laptop.
    case "$project" in
      sowsprint-dev-*)
        who="${project#sowsprint-dev-}"
        printf '  %-24s %s container(s)  %sVS Code dev container — %s%s\n' \
          "$project" "$count" "$C_DIM" "$who" "$C_RESET"
        ;;
      *)
        printf '  %-24s %s container(s)\n' "$project" "$count"
        ;;
    esac
    docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}}' \
      | sed 's/^/      /'
  done
fi
