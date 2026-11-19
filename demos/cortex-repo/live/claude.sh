#!/bin/zsh
# Start Claude Code in the demo repository, through the dev Cortex. Extra arguments go to claude.
source "${0:A:h}/_common.sh"
cd "$CLAUDE_REPO" || { echo "Run ./reset.sh claude first"; exit 1; }
# No auto memory: a note Claude Code saves about the repository's hooks would be loaded by the next
# session at the same path (reset.sh keeps the path), and it would no longer be rejected.
exec env CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 claude --settings "$WALK/claude-dev.json" --model "$MODEL" --setting-sources "" --strict-mcp-config \
  --allowedTools Bash Read Write Edit --permission-mode acceptEdits "$@"
