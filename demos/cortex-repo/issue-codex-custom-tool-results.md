<!-- Draft issue for rossoctl/cortex. Not filed yet. -->

# Codex tool results are not recorded: the Responses parser ignores `custom_tool_call_output`

## What happens

Cortex records every tool call a Codex session makes, but none of the results. In a recorded
Codex session with 13 tool calls, `InferenceExtension.ToolResults` was empty on every request:
13 calls, 0 results. agentop's timeline shows each Codex command with no output, and a commit
that a git hook rejected shows no rejection.

Claude Code sessions through the same Cortex are recorded correctly.

## Cause

Codex declares its tools as custom (freeform) tools. Codex 0.167 sends one `exec` tool whose
input is a JavaScript snippet that calls `tools.exec_command(...)`. So every tool result it
sends back is a `custom_tool_call_output` input item, not a `function_call_output` item.

`responsesInputItem.UnmarshalJSON` in `core/parse/inference/responses.go` only reads
`function_call_output`:

```go
if raw.Type == "function_call_output" {
	it.ToolResults = append(it.ToolResults, model.InferenceToolResult{...})
}
```

So `custom_tool_call_output` items are skipped. The response side already reads Codex's
`custom_tool_call` items (that's why the calls are recorded), so only the results are missing.
The praxis Rust port (`praxis/filters/src/parse/inference/responses.rs`) has the same check.

The two item types have the same `call_id` and `output` fields. These are the two items from a
captured Codex request, with the snippet shortened:

```json
{"type":"custom_tool_call","call_id":"call_2fmY…","name":"exec",
 "input":"const r = await tools.exec_command({cmd:\"cat a.txt\", …});\ntext(r.output);\n"}
{"type":"custom_tool_call_output","call_id":"call_2fmY…",
 "output":[{"type":"input_text","text":"Script completed\nWall time 0.2 seconds\nOutput:\n"},
           {"type":"input_text","text":"hello\n"}]}
```

The output is an array of `input_text` parts, which `flattenResponsesContent` already reads.

## Impact

Anything that reads `ToolResults` gets nothing for Codex:

- agentop's timeline shows no output for Codex tool calls.
- CPEX builds its tool-result parts from `ToolResults` (`inferenceToCMFParts`), so policies and
  redaction on tool output never see Codex's.
- Plugins that learn from or audit sessions see Codex's commands but not what they returned.

## Steps to reproduce

1. Route Codex through Cortex (`agentop configure codex enable`), with Codex 0.167.
2. Run one task that uses a tool:

   ```sh
   mkdir -p /tmp/codex-repro && cd /tmp/codex-repro && echo hello > a.txt
   codex exec --skip-git-repo-check "Run 'cat a.txt' and tell me what it prints." < /dev/null
   ```

3. Count tool calls and tool results in the newest Codex session:

   ```sh
   API=http://127.0.0.1:47601
   S=$(curl -s --noproxy '*' "$API/v1/sessions?archived=true" \
     | jq -r '[.sessions[] | select(.agent=="codex")] | max_by(.createdAt) | .id')
   curl -s --noproxy '*' "$API/v1/sessions/$S?limit=5000" | jq -r '
     [.events[] | select(.inference)] as $e
     | "tool calls=\([$e[] | .inference.toolCalls[]?.id] | unique | length)  results=\([$e[] | .inference.toolResults[]?.toolUseId] | unique | length)"'
   ```

**Expected:** results equal tool calls. **Actual:** `results=0`.

## Environment

- macOS, Darwin arm64
- Cortex / agentop v0.9.5. The same code is on `main` at `28ab6a78`.
- Codex CLI 0.167.0, Responses API through a LiteLLM proxy (`wire_api = "responses"`)

## Proposed fix

Read `custom_tool_call_output` the same way as `function_call_output`, in the Go parser and
in the praxis port. A PR is ready.

Not covered by that change, and maybe worth separate issues:

- The script plugin's Python SDK (`core/plugins/script/sdk/cortex.py`) also only matches
  `function_call_output`, so script plugins don't see Codex tool output either.
- The CPEX README's table of tool-output sources lists only `function_call_output`.
