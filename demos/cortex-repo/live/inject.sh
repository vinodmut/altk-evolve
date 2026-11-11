#!/bin/zsh
# Turn guideline injection on or off in the dev Cortex, live: ./inject.sh on|off|status
source "${0:A:h}/_common.sh"
show() { echo "Evolve guidelines: $(plugin_config | jq -r 'if .inject == false then "OFF" else "ON" end')"; }
case "$1" in
  on|off) before=$(reloads); set_plugin_key inject $([ "$1" = on ] && echo true || echo false); wait_reload "$before" && show ;;
  status) show ;;
  *) echo "usage: $0 on|off|status"; exit 2 ;;
esac
