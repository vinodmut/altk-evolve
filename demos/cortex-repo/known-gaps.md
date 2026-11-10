# Known gaps in what Cortex records

Gaps found while running the demos that affect what Evolve can learn from. Each says what's
missing, when it matters, and what would close it.

## A tool result is recorded only when the agent sends another request

**What.** Cortex reads tool results from the *request* side: an agent runs a tool locally,
then sends the output back to the model in its next request (`tool_result` blocks on the
Anthropic API, `function_call_output` / `custom_tool_call_output` items on the Responses
API). The tool call itself is read from the *response* that asked for it. So a call's result
reaches Cortex one request later than the call, and only if there is a later request.

**When it matters.** Not for a session that ends normally: agents finish with a text answer,
and the request for that answer already carries every earlier tool result. It matters when a
session ends straight after a tool call:

- the user interrupts the agent (Ctrl-C, closing the terminal) while a tool runs or just
  after;
- the agent or its harness crashes, times out, or hits a budget limit;
- `codex exec` / `claude -p` runs that are killed by a driver script before the final turn.

In those cases the last call is recorded with no result. Often that is the most informative
step: the command that failed and made the user give up.

**Effect on learning.** The evolve plugin's recorder (`core/plugins/evolve/trajectory.go`)
keeps the call in `calls` waiting for a result that never arrives, and the trajectory it sends
ends on an assistant turn whose tool call has no `tool` message. Evolve still processes it,
but without the error text, which is what it learns from.

**What would close it.** Cortex can't see a result the agent never sends. Options, roughly in
order of effort:

1. Note it in the trajectory: when an idle session's batch ends on an unanswered call, add a
   marker such as `[no result recorded: session ended after this call]`, so Evolve knows the
   session was cut short rather than treating the call as successful.
2. Use the harness's own record where there is one (Claude Code and Codex both write local
   session logs), as an optional enrichment outside the proxy path.
3. Accept it: interrupted sessions are a minority, and complete sessions are unaffected.

**Applies to** every agent and both API dialects; it is how the proxy position works, not a
parser bug. Checked on Nov 10: in a Codex session that ended normally, the final request
carried all 13 tool results, so complete sessions are indeed unaffected.

## Most Codex turns are left out of what Evolve learns from (open)

**What.** Codex closes each streaming connection as soon as it has read `response.completed`,
before Cortex has finished relaying the stream. Cortex records that response with
`error.kind: client_canceled` and resets the request's status to 0, so the outcome every
finisher sees is "error". The evolve plugin's `OnFinish` (`core/plugins/evolve/plugin.go`)
only records a turn whose outcome status is 2xx, so it drops these turns, even though the
response arrived whole (`finishReason: completed`, full tool call). Claude Code doesn't do
this: 0 of 41 of its responses in two recorded sessions were marked `client_canceled`.

**Measured** (Nov 10, three Codex sessions through the fixed Cortex): 7–8 responses per
session (of 13–14) were marked `client_canceled`. In the session where the sent batch was
captured, the recorder kept 5 of 12 tool calls. Evolve was sent 11–13 messages per session,
where replaying the same sessions in full gives 27–29. Evolve still learned
useful guidelines from what it got (for example "check commit-message format requirements by
inspecting commit hooks"), but from less than half the session.

**What would close it.** In `OnFinish`, accept a turn that wasn't denied and whose inference
extension shows a finished response (a `finishReason`, or a completion or tool calls), even
when the outcome is an error because the client hung up after the last frame. Better still,
the listener could leave the status alone when the client hangs up after the terminal frame,
but that changes what every finisher sees, so it's a Cortex core question.

## Codex tool results weren't recorded (fixed)

Codex 0.167 declares its tools as custom (freeform) tools, so its results arrive as
`custom_tool_call_output` items. Cortex's Responses parser only read `function_call_output`,
so it recorded Codex's tool calls but none of their results (13 calls, 0 results in the
[transfer probe](reports/codex-transfer-20261109/report.html)). Fixed on the Cortex
`evolve-plugin` branch in `core/plugins/inferenceparser/responses.go`.
