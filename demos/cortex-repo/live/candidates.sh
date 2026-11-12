#!/bin/zsh
# What Evolve learned: the learn namespace (LEARN_NS, never served):
#   ./candidates.sh list | show <id> | wait [seconds] | wait-and-promote [seconds] | clear
source "${0:A:h}/_common.sh"
ids() { ev entities export "$LEARN_NS" 2>/dev/null | jq -r '.entities[]?.id' 2>/dev/null; }

# Waits until candidates appear that weren't there at the start, then until no more arrive for a
# few seconds (one session's batch is written as several entities), and prints the new IDs.
wait_new() {
  local limit="${1:-300}" t0=$SECONDS before=(${(f)"$(ids)"}) now new last=""
  echo "Waiting for Cortex to send the idle session and Evolve to learn (up to ${limit}s)..." >&2
  while [ $((SECONDS - t0)) -lt "$limit" ]; do
    now=(${(f)"$(ids)"}); new=(${now:|before})
    if [ ${#new} -gt 0 ]; then
      [ "${new[*]}" = "$last" ] && { echo >&2; print -l -- $new; return 0; }
      last="${new[*]}"; sleep 4
    else
      sleep 5
    fi
    printf '.' >&2
  done
  echo >&2; echo "Nothing new after ${limit}s. Check the dev Cortex log for 'sent idle session' and the Evolve log." >&2
  return 1
}

case "$1" in
  list) ev entities list "$LEARN_NS" --type guideline ;;
  show) ev entities show "$LEARN_NS" "$2" ;;
  wait) wait_new "$2" >/dev/null && ev entities list "$LEARN_NS" --type guideline ;;
  wait-and-promote)
    # The review gate, skipped: everything the session taught is served to the next session.
    new=(${(f)"$(wait_new "$2")"}) || exit 1
    echo "Learned ${#new} guideline(s); promoting all of them from $LEARN_NS to $SERVE_NS:"
    "${0:A:h}/promote.sh" $new >/dev/null || exit 1
    ev entities list "$SERVE_NS" --type guideline ;;
  clear)
    for id in ${(f)"$(ids)"}; do ev entities delete "$LEARN_NS" "$id" >/dev/null; done
    echo "Candidates cleared." ;;
  *) echo "usage: $0 list | show <id> | wait [seconds] | wait-and-promote [seconds] | clear"; exit 2 ;;
esac
