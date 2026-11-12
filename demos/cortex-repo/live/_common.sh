# Shared settings for the live-demo scripts. Source your env.sh first; it sets WALK and the ports.
: "${WALK:?Source your env.sh first (it sets WALK)}"
# The manual walkthrough's ports, unless env.sh sets others.
CORTEX_PROXY="${CORTEX_PROXY:-47700}"; CORTEX_API="${CORTEX_API:-47701}"; CORTEX_STATS="${CORTEX_STATS:-47702}"
EVOLVE_ENV_FILE="${EVOLVE_ENV_FILE:-$WALK/evolve.env}"            # the running Evolve server's settings
CORTEX_CONFIG="${CORTEX_CONFIG:-$WALK/cortex-home/.cortex/config.yaml}"
CODEX_HOME_DIR="${CODEX_HOME_DIR:-$WALK/codex-home}"               # Codex home routed to the dev Cortex
CODEX_USER_HOME="${CODEX_USER_HOME:-$WALK/codex-user}"             # HOME for Codex: no personal skills
# One demo repository per agent, so each run's commits stay visible.
DEMO_ROOT="${DEMO_ROOT:-$WALK/live}"
CLAUDE_REPO="$DEMO_ROOT/claude-shopkit"
CODEX_REPO="$DEMO_ROOT/codex-shopkit"
LIVE_DIR="${0:A:h}"
SHOPKIT="${LIVE_DIR:h}/shopkit"
DEFAULT_GUIDELINES="${LIVE_DIR:h}/guidelines/rules-20261111.json"

# The Evolve CLI with the server's settings, outside any proxy.
ev() {
  (set -a; . "$EVOLVE_ENV_FILE"; set +a; cd "$WALK" && env -u HTTPS_PROXY -u HTTP_PROXY -u https_proxy -u http_proxy \
    uv run -q --project "$EVOLVE_SRC" evolve "$@" 2>&1 | grep -v -E 'LiteLLM|^INFO')
}

# Set one key in the evolve plugin's config block; Cortex reloads the file by itself.
set_plugin_key() {
  python3 - "$CORTEX_CONFIG" "$1" "$2" <<'PY'
import re, sys
path, key, value = sys.argv[1:]
lines = open(path).read().split("\n")
start = next(i for i, l in enumerate(lines) if re.match(r"\s*- name: evolve\s*$", l))
cfg = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == "config:")
indent = len(lines[cfg + 1]) - len(lines[cfg + 1].lstrip())
end = cfg + 1
while end < len(lines) and (not lines[end].strip() or len(lines[end]) - len(lines[end].lstrip()) >= indent):
    end += 1
for i in range(cfg + 1, end):
    if lines[i].strip().startswith(key + ":"):
        lines[i] = " " * indent + f"{key}: {value}"
        break
else:
    lines.insert(end, " " * indent + f"{key}: {value}")
open(path, "w").write("\n".join(lines))
PY
}

reloads() { curl -s --noproxy '*' "127.0.0.1:$CORTEX_STATS/reload/status" | jq -r .reloads_ok; }
wait_reload() {
  local before=$1
  for i in $(seq 1 40); do [ "$(reloads)" != "$before" ] && break; sleep 0.25; done
  local err=$(curl -s --noproxy '*' "127.0.0.1:$CORTEX_STATS/reload/status" | jq -r .last_error)
  [ -z "$err" ] || { echo "Cortex rejected the config: $err"; return 1; }
}
plugin_config() { curl -s --noproxy '*' "127.0.0.1:$CORTEX_API/v1/pipeline" | jq -c '.outbound[] | select(.name=="evolve") | .config'; }
