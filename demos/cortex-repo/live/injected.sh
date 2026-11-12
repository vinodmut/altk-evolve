#!/bin/zsh
# Did Cortex inject guidelines into an agent's latest session? ./injected.sh [codex|claude-code] [session-id]
source "${0:A:h}/_common.sh"
agent="${1:-codex}"; [ "$agent" = claude ] && agent=claude-code
api="127.0.0.1:$CORTEX_API"
S="${2:-$(curl -s --noproxy '*' "$api/v1/sessions?archived=true" | jq -r --arg a "$agent" '[.sessions[] | select(.agent==$a)] | max_by(.createdAt) | .id // empty')}"
[ -n "$S" ] || { echo "No $agent session in the dev Cortex yet."; exit 1; }
events=$(curl -s --noproxy '*' "$api/v1/sessions/$S?limit=5000")
# The first main-agent request: the one the plugin injects into.
first=$(print -r -- "$events" | jq -c '[.events[] | select(.phase=="request" and .inference and ((.inference.tools // []) | length) > 0 and (.inference.agentRole // "main") != "subagent")][0]')
[ "$first" != null ] || { echo "Session $S has no main-agent request."; exit 1; }
echo "Session $S ($agent), request #$(print -r -- "$first" | jq -r .seq)"
echo
echo "1. What the evolve plugin recorded (plugins.evolve):"
print -r -- "$first" | jq '.plugins.evolve // "nothing: the plugin did not act on this request"'
echo
echo "2. Its invocation:"
print -r -- "$first" | jq -r '[.invocations.outbound[]? | select(.plugin=="evolve") | "   \(.action)\(if .reason then " · " + .reason else "" end)"] | if length == 0 then "   none" else .[] end'
echo
echo "3. The guidelines block in the body Cortex forwarded to the model:"
seq=$(print -r -- "$first" | jq -r .seq)
curl -s --noproxy '*' "$api/v1/sessions/$S/events/$seq?raw=1" | jq -r '
  if .raw.forwarded == null then "   none: the request was forwarded as the agent sent it"
  else (.raw.forwarded | fromjson? // .raw.forwarded) | [.. | strings | select(test("<evolve-guidelines"))][0]
       | if . == null then "   no block found" else capture("(?s)(?<b><evolve-guidelines.*?</evolve-guidelines>)").b end
  end'
