#!/bin/zsh
# Every entity in the serve and learn namespaces, as JSON on stdout: ./entities.sh | jless
#   ./entities.sh            both, as {"<serve namespace>": [...], "<learn namespace>": [...]}
#   ./entities.sh served     the serve namespace only (also: learned, or any namespace name)
source "${0:A:h}/_common.sh"
# Only JSON reaches stdout; the CLI's warnings and log lines go to stderr.
export_ns() {
  local out=$( (set -a; . "$EVOLVE_ENV_FILE"; set +a; cd "$WALK" && env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy \
    uv run -q --project "$EVOLVE_SRC" evolve entities export "$1" --type "") 2>/dev/null | jq '.entities' 2>/dev/null)
  [ -n "$out" ] && print -r -- "$out" || { echo "No namespace '$1'." >&2; echo '[]'; }
}
case "${1:-both}" in
  both)    jq -n --arg s "$SERVE_NS" --arg l "$LEARN_NS" --argjson sv "$(export_ns "$SERVE_NS")" --argjson lv "$(export_ns "$LEARN_NS")" '{($s): $sv, ($l): $lv}' ;;
  served)  export_ns "$SERVE_NS" ;;
  learned) export_ns "$LEARN_NS" ;;
  -h|--help) sed -n '2,4p' "$0" | sed 's/^# //' ;;
  *)       export_ns "$1" ;;
esac
