#!/bin/zsh
# agentop observe on the dev Cortex, not your everyday one: ./observe.sh [--print] [agentop args]
#   --print   show the command instead of running it
source "${0:A:h}/_common.sh"
AGENTOP="${AGENTOP:-$(command -v agentop)}"
[ -n "$AGENTOP" ] || { echo "agentop not found; set AGENTOP to its path"; exit 1; }
cmd=("$AGENTOP" observe -endpoint "http://127.0.0.1:$CORTEX_API")
if [ "$1" = --print ]; then
  shift; print -r -- "${(q-)cmd[@]}${@:+ ${(q-)@}}"; exit 0
fi
echo "Running: ${cmd[*]}${@:+ $*}"
exec "${cmd[@]}" "$@"
