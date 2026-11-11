#!/bin/zsh
# Start Codex in the demo repository, through the dev Cortex. Extra arguments go to codex.
source "${0:A:h}/_common.sh"
cd "$DEMO_REPO" || { echo "Run ./reset.sh first"; exit 1; }
exec env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy \
  HOME="$CODEX_USER_HOME" CODEX_HOME="$CODEX_HOME_DIR" codex "$@"
