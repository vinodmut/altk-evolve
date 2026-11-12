#!/bin/zsh
# Put a demo repository back to its starting state: a fresh shopkit with one commit.
#   ./reset.sh claude | codex      (no argument: both)
source "${0:A:h}/_common.sh"
# Empties an existing directory rather than replacing it, so a terminal already inside it keeps
# working (a deleted directory leaves that shell in a path that no longer exists).
fresh() {
  mkdir -p "$1" && find "$1" -mindepth 1 -maxdepth 1 -exec rm -rf {} + && cp -R "$SHOPKIT"/. "$1"/ && cd "$1" \
    && git init -q && git config core.hooksPath .githooks && git add -A \
    && git -c user.name=dev -c user.email=dev@shopkit.test commit -q -m "chore: shopkit 0.4.0" >/dev/null 2>&1 \
    && echo "Fresh repo: $1  ($(git log --oneline | head -1))"
}
case "${1:-both}" in
  claude) fresh "$CLAUDE_REPO" ;;
  codex) fresh "$CODEX_REPO" ;;
  both) fresh "$CLAUDE_REPO" && fresh "$CODEX_REPO" ;;
  *) echo "usage: $0 [claude|codex]"; exit 2 ;;
esac
