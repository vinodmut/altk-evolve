#!/bin/zsh
# Is the already-running setup ready for the live demo? Checks each piece and says how to fix it.
source "${0:A:h}/_common.sh"
EVOLVE_PORT="${EVOLVE_PORT:-8201}"
fails=0
ok()   { print -P "%F{green}✓%f $1"; }
bad()  { print -P "%F{red}✗%f $1"; print "    → $2"; fails=$((fails + 1)); }
warn() { print -P "%F{yellow}!%f $1"; print "    → $2"; }

curl -s --noproxy '*' -m3 -o /dev/null -w '%{http_code}' "127.0.0.1:$EVOLVE_PORT/sse" 2>/dev/null | grep -q 200 \
  && ok "Evolve MCP server on $EVOLVE_PORT" || bad "Evolve MCP server not answering on $EVOLVE_PORT" "start it (walkthrough-manual.html, step 3) or set EVOLVE_PORT in env.sh"

[ -f "$EVOLVE_ENV_FILE" ] && ok "Evolve settings: ${EVOLVE_ENV_FILE/#$HOME/~}" || bad "No Evolve env file at $EVOLVE_ENV_FILE" "set EVOLVE_ENV_FILE in env.sh to the file the server was started with"
if [ -f "$EVOLVE_ENV_FILE" ]; then
  grep -q '^EVOLVE_GUIDELINES_PROMPT=rules' "$EVOLVE_ENV_FILE" && ok "Evolve learns with the rules prompt" \
    || warn "Evolve uses the default prompt" "add EVOLVE_GUIDELINES_PROMPT=rules to $EVOLVE_ENV_FILE and restart Evolve, for rules like 'add an entry under ## Unreleased'"
fi

cfg=$(plugin_config 2>/dev/null)
if [ -z "$cfg" ]; then
  bad "Dev Cortex not answering on $CORTEX_API, or no evolve plugin" "start the dev Cortex (walkthrough-manual.html, step 4) or set CORTEX_API in env.sh"
else
  ok "Dev Cortex on $CORTEX_PROXY with the evolve plugin: learns into $LEARN_NS, serves $SERVE_NS"
  ep=$(print -r -- "$cfg" | jq -r .endpoint)
  [[ "$ep" == *":$EVOLVE_PORT/"* ]] && ok "Plugin talks to Evolve at $ep" || bad "Plugin endpoint is $ep" "point the evolve plugin's endpoint at http://127.0.0.1:$EVOLVE_PORT/sse"
  print -r -- "$cfg" | jq -e '.agents | index("codex")' >/dev/null && ok "Plugin acts on codex as well as claude-code" || bad "Plugin agents don't include codex" "set agents: [claude-code, codex] in the evolve plugin config"
  [ "$(print -r -- "$cfg" | jq -r .placement)" = user ] && ok "Guidelines go in the first user message (placement: user)" || warn "placement is not user" "set placement: user; the model ignores guidelines at the end of Claude Code's system prompt"
  print -r -- "$cfg" | jq -e '.inject == false' >/dev/null && warn "Injection is OFF" "./inject.sh on, unless you mean it" || ok "Injection on"
fi
grep -q '"Session-Id"' "$CORTEX_CONFIG" 2>/dev/null && ok "Cortex groups Codex sessions (Session-Id header)" || bad "Session-Id not in session.id_headers" "add it to $CORTEX_CONFIG and restart the dev Cortex"

[ -f "$WALK/claude-dev.json" ] && ok "Claude Code routing: ${WALK/#$HOME/~}/claude-dev.json" || bad "No $WALK/claude-dev.json" "create it (walkthrough-manual.html, step 5)"
[ -n "$MODEL" ] && ok "Claude Code model: $MODEL" || bad "MODEL is not set" "set MODEL in env.sh"
[ -x "$WALK/shopkit-venv/bin/python" ] && ok "shopkit virtualenv: ${WALK/#$HOME/~}/shopkit-venv" || bad "No shopkit virtualenv" "create it (walkthrough-manual.html, step 2)"

if [ -f "$CODEX_HOME_DIR/config.toml" ] && [ -f "$CODEX_HOME_DIR/.env" ]; then
  grep -q "127.0.0.1:$CORTEX_PROXY" "$CODEX_HOME_DIR/.env" && ok "Codex routed to the dev Cortex (${CODEX_HOME_DIR/#$HOME/~})" \
    || bad "Codex's .env doesn't point at 127.0.0.1:$CORTEX_PROXY" "fix HTTPS_PROXY in $CODEX_HOME_DIR/.env, then restart Codex's background process (live-demo.html, Codex setup)"
  grep -q "\"$CODEX_REPO\"" "$CODEX_HOME_DIR/config.toml" && ok "Codex trusts its demo repository" || warn "Codex doesn't list $CODEX_REPO as trusted" "add [projects.\"$CODEX_REPO\"] trust_level = \"trusted\" to its config.toml"
else
  bad "No Codex home at $CODEX_HOME_DIR" "set it up (live-demo.html, Codex through the dev Cortex)"
fi
[ -f "$CODEX_USER_HOME/.zprofile" ] && ok "Codex HOME with the virtualenv on PATH" || bad "No $CODEX_USER_HOME/.zprofile" "set it up (live-demo.html, Codex through the dev Cortex)"

s=$(ev entities list "$SERVE_NS" --type guideline 2>/dev/null | grep -o 'Total: [0-9]*' | grep -o '[0-9]*'); print "  Evolve serves ${s:-0} guidelines; ./guidelines.sh list to see them."
[ $fails -eq 0 ] && print -P "%F{green}Ready.%f" || { print -P "%F{red}$fails problem(s).%f"; exit 1; }
