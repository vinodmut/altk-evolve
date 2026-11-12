#!/bin/zsh
# Review gate: copy chosen candidates into the served namespace, with their provenance.
#   ./promote.sh <candidate-id> [<candidate-id> ...]
source "${0:A:h}/_common.sh"
[ $# -gt 0 ] || { echo "usage: $0 <candidate-id> [...]"; exit 2; }
tmp=$(mktemp -t promote).json
ev entities export "$LEARN_NS" \
  | jq --argjson ids "$(printf '%s\n' "$@" | jq -R . | jq -s .)" '{entities: [.entities[] | select(.id as $i | $ids | index($i))]}' > "$tmp" || exit 1
n=$(jq '.entities | length' "$tmp")
[ "$n" = "$#" ] || { echo "Found $n of $# candidates; check the IDs with ./candidates.sh list"; rm -f "$tmp"; exit 1; }
ev entities import "$SERVE_NS" "$tmp" --append; rm -f "$tmp"
