#!/bin/zsh
# Each demo repository's commits and what the last one changed: ./commits.sh [claude|codex]
source "${0:A:h}/_common.sh"
show() {
  print -P "%B== $1: ${2/#$HOME/~}%b"
  [ -d "$2/.git" ] || { echo "   (not created yet: ./reset.sh $1)"; return; }
  git -C "$2" log --oneline
  [ "$(git -C "$2" rev-list --count HEAD)" -gt 1 ] && git -C "$2" show --stat --format='%n%s' HEAD | sed 's/^/   /'
  echo
}
case "${1:-both}" in
  claude) show claude "$CLAUDE_REPO" ;;
  codex) show codex "$CODEX_REPO" ;;
  *) show claude "$CLAUDE_REPO"; show codex "$CODEX_REPO" ;;
esac
