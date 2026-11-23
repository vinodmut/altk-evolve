# Running the evolve loop with your installed Cortex

This walks through the [Cortex `evolve` plugin](https://github.com/rossoctl/cortex/blob/main/docs/evolve-plugin.md)
with the Cortex you already have installed and running, and an altk-evolve server run with
`uvx`, with no Cortex build and no evolve checkout. By the end you will have watched the whole
loop:

```
claude -p ──► your Cortex (47600) ──► LLM gateway
                 │   ▲
   process_      │   │ get_guidelines_with_attribution
   trajectory    ▼   │
              evolve-mcp (8201) ── cortex-candidates  (learned, not injected)
                                └─ cortex             (reviewed, injected)
```

1. a guideline you put in `cortex` is injected into a Claude Code request,
2. the session is sent to evolve when it goes idle and evolve learns candidates from it,
3. you promote a candidate and the next session receives it,
4. you withdraw a guideline and the next session no longer does.

Allow about 15 minutes.

> **This changes your everyday Cortex while it is on.** The plugin acts on every Claude Code
> session that goes through your Cortex, not only the ones in this walkthrough: each one gets
> the guidelines in `cortex` injected, and is sent to evolve to learn from, which means its
> content goes to evolve's LLM. The canary in step 4 shows up in all of them until you withdraw
> it in step 7, so do that promptly. To stop everything at once, remove the `evolve` entry
> (see [Turning it off](#turning-it-off)).

## What you need

| | |
|---|---|
| Your installed Cortex, running, with the plugin | built from Cortex main, or a release with the `evolve` plugin. Step 1 checks |
| Claude Code routed through it | `agentop claudecode enable` (plain `claude` then goes through Cortex) |
| uv | `uv --version`. Its `uvx` runs evolve without a checkout |
| altk-evolve with `--local` | For now, the `feat/local-preset` branch of `vinodmut/altk-evolve` (pinned below); later, the first release with it |
| LLM credentials for evolve | any litellm model; evolve calls it to generate guidelines |

The commands below use these variables; set them in each terminal:

```bash
export DEV=/tmp/cortex-evolve          # scratch dir for this walkthrough
export EVOLVE_HOME=$DEV/evolve         # evolve's home for --local: its settings and data
export EVOLVE_SPEC="altk-evolve-core[server,cli,fastembed,tracing] @ git+https://github.com/vinodmut/altk-evolve@7aad156053cd44ca54b95dcc1d6d3516171bc5b2"
ev() { uvx -q --from "$EVOLVE_SPEC" evolve --local "$@"; }
```

`EVOLVE_SPEC` is what `uvx` installs evolve from: the fork's commit, pinned so `uvx` doesn't
serve a stale build of a branch. Once a release has `--local`, it becomes `altk-evolve`. `ev`
runs the evolve CLI with `--local`, so it uses `$EVOLVE_HOME`, the same settings and data as
the server, from any directory.

You will use **two terminals**: one for evolve, one to drive.

---

## 1. Check your Cortex has the plugin

```bash
curl -s --noproxy '*' 127.0.0.1:47601/v1/plugins | jq -r '[.. | objects | select(.name? == "evolve" and has("fields"))] | length'
```

**Look for** `1`. A `0` means your Cortex was built without the plugin; update it.

## 2. Write evolve's settings file

```bash
umask 077 && mkdir -p $EVOLVE_HOME
cat > $EVOLVE_HOME/config.env <<'EOF'
EVOLVE_MODEL_NAME=litellm_proxy/aws/claude-sonnet-5
LITELLM_PROXY_API_BASE=https://<your litellm gateway>
LITELLM_PROXY_API_KEY=
EVOLVE_PROXY=none
EOF
```

Then open `$EVOLVE_HOME/config.env` and add your gateway's key to the `LITELLM_PROXY_API_KEY` line.

**What each line does.**

- `EVOLVE_MODEL_NAME` + the `LITELLM_*` pair — the model evolve uses for guideline generation
  and conflict resolution. Any litellm model string works; use whichever credentials you have
  (`OPENAI_API_KEY`, etc.).
- `EVOLVE_PROXY=none` — evolve's own connections go direct. Your shell, or anything Claude Code
  starts, may carry `HTTPS_PROXY` pointing at your Cortex, and evolve's LLM calls would then go
  through it and show up as traffic in agentop.

`--local` (next step) fills in the rest: the filesystem backend with its data and database
under `$EVOLVE_HOME`, apart from any evolve data you already have; `EVOLVE_INJECTION_MODE=retrieval`,
which the plugin needs; and telemetry off.

The file holds a key, hence `umask 077`; evolve warns at startup if others can read it.

## 3. Start evolve's MCP server — terminal 1

```bash
uvx --from "$EVOLVE_SPEC" evolve-mcp --local --transport sse --port 8201
```

**Look for** — the first run installs evolve (about 20 s) and downloads a small embedding
model. The server reports its settings to stderr:

```
Evolve home: /tmp/cortex-evolve/evolve
  settings file: /tmp/cortex-evolve/evolve/config.env (4 set from it)
  backend: filesystem, data: /tmp/cortex-evolve/evolve/data
  database: /tmp/cortex-evolve/evolve/evolve.sqlite.db
  injection mode: retrieval
  hooks: the usual lookup (./evolve.hooks.yaml, ~/.config/evolve/hooks.yaml)
  model: litellm_proxy/aws/claude-sonnet-5
  proxy: none (direct)
```

then `Uvicorn running on http://127.0.0.1:8201`. A `warning:` line under the report means
something to fix first: a settings file others can read, a local proxy evolve would use, or no
credentials for the model.

**Check it** from terminal 2: `curl -s --noproxy '*' -m2 -o /dev/null -w '%{http_code}\n' 127.0.0.1:8201/sse`
prints `200` (the request then times out holding the stream open — that is normal).

> **Security.** This server has no authentication: any local process can read and change the
> guidelines it serves. It binds loopback only. Don't run it on a shared machine.

## 4. Turn the plugin on in your Cortex — terminal 2

Keep a copy of your config, then open `~/.cortex/config.yaml` in an editor:

```bash
cp ~/.cortex/config.yaml ~/.cortex/config.yaml.before-evolve
```

Under `pipeline:` → `outbound:` → `plugins:`, add this as the **last** entry, indented like
the entries above it:

```yaml
      - name: evolve
        config:
          endpoint: http://127.0.0.1:8201/sse
          agents: [claude-code]
          idle_after_seconds: 60
          cache_ttl_seconds: 120
```

Edit by hand rather than with a script: a YAML round trip would drop your config's comments.

- **Last**, because the plugin rewrites the request body, and body readers such as the parsers
  must come before it.
- `agents: [claude-code]` limits it to Claude Code; add `codex` to include Codex.
- `idle_after_seconds: 60` and `cache_ttl_seconds: 120` shorten the defaults (15 and 30
  minutes) so the loop runs in minutes. Leave them out for everyday use.
- Learned guidelines go to `cortex-candidates`, and only `cortex`, which you review, is
  injected: the defaults keep anything learned out of your sessions until you accept it.

Cortex reloads its config when you save. **Check** that the plugin is in the pipeline, last:

```bash
curl -s --noproxy '*' 127.0.0.1:47601/v1/pipeline | jq -c '[.outbound[].name]'
# [..., "tool-prune", "script", "evolve"]
```

**If** it isn't there, `grep -i -E 'evolve|reload' ~/.cortex/proxy.log | tail -5` shows why: a
config error names the field, and Cortex keeps running on the config it had.

## 5. Seed one guideline and watch it get injected — terminal 2

Evolve starts empty, so put one guideline into the reviewed namespace by hand. A *canary* —
an instruction with a visible effect — proves the text reached the model:

```bash
ev namespaces create cortex
ev entities add cortex --no-conflict-resolution \
  -c "When you finish answering, end your reply with the exact line: EVOLVE-CANARY-7731" \
  -m '{"creation_mode":"manual","task_description":"any task in a scratch directory"}'
```

Now run Claude Code as you normally would, in a throwaway directory:

```bash
mkdir -p $DEV/scratch && cd $DEV/scratch && echo hello > a.txt && echo world > b.txt
claude -p "List the files in this directory and tell me what each contains." \
  --allowedTools "Bash(ls:*)" "Bash(cat:*)" Read
```

**Look for** the canary as the reply's last line:

```
| `a.txt` | `hello` |
| `b.txt` | `world` |

EVOLVE-CANARY-7731
```

**Then look at what the plugin recorded.** Your Cortex has all your sessions; right after the
run, the newest Claude Code session is this one:

```bash
S=$(curl -s --noproxy '*' 127.0.0.1:47601/v1/sessions | jq -r '[.sessions[] | select(.agent == "claude-code")] | max_by(.createdAt) | .id')
curl -s --noproxy '*' 127.0.0.1:47601/v1/sessions/$S | jq -c '.events[] | select(.phase=="request" and .inference)
  | {seq, evolve: .plugins.evolve, why: [.invocations.outbound[]? | select(.plugin=="evolve") | .reason]}'
```

```
{"seq":2,"evolve":{"injected":true,"ids":["1"],"cached":false,…},"why":["body_rewritten"]}
{"seq":4,"evolve":{"injected":true,"ids":["1"],"cached":true,…},"why":["body_rewritten"]}
{"seq":6,"evolve":null,"why":["no_tools"]}
```

- `injected: true` + `body_rewritten` — the block was added to this request.
- `ids` — exactly which guidelines shaped it. `sources` (empty here) names the session batch a
  learned guideline came from.
- `cached: false` on the first request, `true` after — evolve is asked once per session; later
  turns reuse the identical block, so prompt caching keeps working.
- `no_tools` — a Claude Code side call (session title, summary). Left alone on purpose.

agentop shows the same: `agentop observe`, then the detail pane of a request.

**If there is no canary**, read the `why` column:

| What you see | Meaning | What to do |
|---|---|---|
| No such session | Claude Code didn't go through your Cortex | `agentop claudecode enable`, or check `~/.claude/settings.json` has `HTTPS_PROXY=http://127.0.0.1:47600` |
| `evolve_unavailable` | The plugin couldn't reach evolve, or a tool failed | `grep 'evolve:' ~/.cortex/proxy.log \| tail -3` gives the reason; check terminal 1 is up and `endpoint` ends in `/sse` |
| `no_guidelines` | Evolve answered with nothing | Check terminal 1's report says `injection mode: retrieval`; check `ev entities list cortex --type guideline` shows the guideline |
| `agent_not_selected` | Not Claude Code | Expected with `agents: [claude-code]` |

A failed fetch is retried after `retry_after_seconds` (60 s); until then that session gets no
guidelines. Start a new `claude -p` to retry immediately.

## 6. Wait for learning

Leave the session alone for about a minute (`idle_after_seconds: 60`, checked every 30 s).

**Look for** in your Cortex's log:

```bash
grep 'evolve:' ~/.cortex/proxy.log | tail -3
# … msg="evolve: sent idle session to learn from" session=<id> messages=6 batch=<instance>.1-1
```

and in terminal 1, the LLM calls that generate guidelines. Generation takes a few seconds to a
minute. Every Claude Code session you run meanwhile is sent too, once it is idle.

**See what was learned:**

```bash
ev entities list cortex-candidates --type guideline
ev entities show cortex-candidates <id>
```

`show` prints the content and the provenance metadata: `creation_mode: auto-cortex`,
`agent_id: claude-code`, `session_id`, and `source_task_id: cortex:<session>:<batch>`.

**Check the gate held:** `ev entities list cortex --type guideline` still shows only your
canary. Nothing learned is injected until you copy it over.

**If nothing is sent:**

| What you see | Meaning | What to do |
|---|---|---|
| No log line after 2 minutes | The session had fewer than `min_turns` (2) successful turns | Use a prompt that makes at least one tool call |
| `… failed; will retry` with an LLM error | Evolve's model credentials are wrong | Fix `$EVOLVE_HOME/config.env`, restart terminal 1; the plugin sends the same batch again after 30 s, then 1, 2, 4 and 8 minutes |
| Sent, but `cortex-candidates` is empty | Evolve judged the run not worth a guideline | Normal for trivial sessions; try a task where the agent hits and recovers from an error |

## 7. Withdraw the canary, then promote a candidate

Withdraw the canary first, so your other sessions stop getting it:

```bash
ev entities delete cortex 1
```

Then promote a candidate. Promotion is a copy into the reviewed namespace; keep the provenance
so `sources` still says where it came from:

```bash
ID=<candidate id>
uvx -q --from "$EVOLVE_SPEC" python - "$ID" <<'EOF'
import sys
from altk_evolve.local import apply_local, apply_proxy
apply_local(); apply_proxy()  # what --local does: $EVOLVE_HOME's settings and data
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity
c = EvolveClient(); g = c.get_entity_by_id("cortex-candidates", sys.argv[1])
keep = {k: g.metadata[k] for k in ("creation_mode", "agent_id", "session_id", "source_task_id", "task_description", "rationale") if k in g.metadata}
[u] = c.update_entities("cortex", [Entity(type="guideline", content=g.content, metadata=keep)], enable_conflict_resolution=False)
c.delete_entity_by_id("cortex-candidates", g.id)
print("promoted as", u.id)
EOF
```

(`ev entities add cortex -c … -m …` does the same if you'd rather paste the content and
metadata by hand.)

Run another `claude -p` (step 5). **Look for** no canary line, and the promoted id in
`plugins.evolve.ids`, with a `sources` entry naming the session it was learned from:

```
{"injected":true,"ids":["2"],"sources":["cortex:927636aa-…:<instance>.1-1"]}
```

A **new** session picks it up immediately. A session already running keeps its block until a
guideline in it is withdrawn or edited; one it doesn't have reaches it in a new session.

---

## Turning it off

Remove the `evolve` entry from `~/.cortex/config.yaml` (Cortex reloads it), or put back the
copy from step 4 if you have changed nothing else since:

```bash
cp ~/.cortex/config.yaml.before-evolve ~/.cortex/config.yaml
```

Then stop evolve in terminal 1 (Ctrl-C), and delete the walkthrough's data:

```bash
rm -rf $DEV                            # evolve's home and data, and the scratch dir
```

A config reload drops a session's turns not yet sent to evolve; the learned guidelines stay in
`$EVOLVE_HOME` until you delete it.

## Quick reference

| Port | What |
|---|---|
| 8201 | evolve MCP server (SSE at `/sse`) |
| 47600 | your Cortex's forward proxy |
| 47601 | your Cortex's session API (`/v1/sessions`, `/v1/pipeline`, `/v1/plugins`) |

| Plugin skip reason | Meaning |
|---|---|
| `body_rewritten` | guidelines injected |
| `no_guidelines` | evolve returned none for this task |
| `no_task` | the request had no task text to look up |
| `evolve_unavailable` | evolve unreachable or a tool failed; forwarded without new guidelines |
| `no_tools` | tool-less side call (title, summary) |
| `subagent` | subagent request |
| `no_session` | no session id on the request |
| `agent_not_selected` | client not in `agents` |
| `path_not_inference` | not an inference endpoint |
| `invalid_body` | request body not valid JSON |
| `unsupported_request_shape` | system prompt in a form the plugin can't extend |
