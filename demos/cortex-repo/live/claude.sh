#!/bin/zsh
# Start Claude Code in the demo repository, through the dev Cortex. Extra arguments go to claude.
source "${0:A:h}/_common.sh"
cd "$CLAUDE_REPO" || { echo "Run ./reset.sh claude first"; exit 1; }
exec claude --settings "$WALK/claude-dev.json" --model "$MODEL" --setting-sources "" --strict-mcp-config \
  --allowedTools Bash Read Write Edit --permission-mode acceptEdits "$@"
