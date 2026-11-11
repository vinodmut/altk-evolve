#!/bin/zsh
# What the dev Cortex serves (Evolve namespace "cortex"):
#   ./guidelines.sh list | clear | load [file]
source "${0:A:h}/_common.sh"
case "$1" in
  list) ev entities list cortex --type guideline ;;
  clear)
    doc=$(ev entities export cortex) || { print -r -- "$doc"; exit 1; }
    ids=$(print -r -- "$doc" | jq -r '.entities[].id') || exit 1
    for id in ${(f)ids}; do ev entities delete cortex "$id" >/dev/null; done
    echo "Served guidelines cleared ($(print -r -- "$doc" | jq '.entities | length') removed)." ;;
  load) ev entities import cortex "${2:-$DEFAULT_GUIDELINES}" --replace ;;
  *) echo "usage: $0 list | clear | load [file]"; exit 2 ;;
esac
