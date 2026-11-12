#!/bin/zsh
# What Evolve learned: the learn namespace (LEARN_NS, never served):
#   ./candidates.sh list | show <id> | wait [seconds] | clear
source "${0:A:h}/_common.sh"
count() { ev entities export "$LEARN_NS" 2>/dev/null | jq '.entities | length' 2>/dev/null || echo 0; }
case "$1" in
  list) ev entities list "$LEARN_NS" --type guideline ;;
  show) ev entities show "$LEARN_NS" "$2" ;;
  wait)
    start=$(count); limit="${2:-300}"; t0=$SECONDS
    echo "Waiting for Cortex to send the idle session and Evolve to learn (up to ${limit}s)..."
    while [ $((SECONDS - t0)) -lt "$limit" ]; do
      [ "$(count)" -gt "$start" ] && { sleep 3; ev entities list "$LEARN_NS" --type guideline; exit 0; }
      sleep 5; printf '.'
    done
    echo; echo "Nothing new after ${limit}s. Check the dev Cortex log for 'sent idle session' and the Evolve log."; exit 1 ;;
  clear)
    ids=$(ev entities export "$LEARN_NS" | jq -r '.entities[].id')
    for id in ${(f)ids}; do ev entities delete "$LEARN_NS" "$id" >/dev/null; done
    echo "Candidates cleared." ;;
  *) echo "usage: $0 list | show <id> | wait [seconds] | clear"; exit 2 ;;
esac
