#!/bin/zsh
# What the dev Cortex serves: the serve namespace (SERVE_NS):
#   ./guidelines.sh list | clear | load [file]
source "${0:A:h}/_common.sh"
case "$1" in
  list) ev entities list "$SERVE_NS" --type guideline ;;
  clear)
    doc=$(ev entities export "$SERVE_NS") || { print -r -- "$doc"; exit 1; }
    ids=$(print -r -- "$doc" | jq -r '.entities[].id') || exit 1
    for id in ${(f)ids}; do ev entities delete "$SERVE_NS" "$id" >/dev/null; done
    echo "Served guidelines cleared ($(print -r -- "$doc" | jq '.entities | length') removed)." ;;
  load) ev entities import "$SERVE_NS" "${2:-$DEFAULT_GUIDELINES}" --replace ;;
  *) echo "usage: $0 list | clear | load [file]"; exit 2 ;;
esac
