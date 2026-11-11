#!/bin/zsh
# Before a demo: guidelines on, learning on, a short idle wait, and empty Evolve namespaces.
#   ./prepare.sh [idle-seconds]      (default 30)
source "${0:A:h}/_common.sh"
idle="${1:-30}"
before=$(reloads)
set_plugin_key inject true; set_plugin_key learn true; set_plugin_key idle_after_seconds "$idle"
wait_reload "$before" || exit 1
for ns in cortex cortex-candidates; do
  ids=$(ev entities export "$ns" 2>/dev/null | jq -r '.entities[]?.id' 2>/dev/null)
  for id in ${(f)ids}; do ev entities delete "$ns" "$id" >/dev/null; done
done
echo "Cortex evolve plugin: $(plugin_config | jq -c '{inject, learn, idle_after_seconds}')"
echo "Served guidelines and candidates cleared."
