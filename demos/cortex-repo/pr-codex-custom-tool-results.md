<!-- Draft PR for rossoctl/cortex, from vinodmut:fix/codex-custom-tool-results. Not opened yet. -->

**Title:** `fix: Read Codex custom_tool_call_output items as tool results`

Fixes #<issue>

## What changed

Codex declares its tools as custom (freeform) tools, so every tool result it sends back is a
`custom_tool_call_output` input item. The Responses request parser only read
`function_call_output`, so Cortex recorded each Codex tool call but none of its results.

- **Fix:** `responsesInputItem.UnmarshalJSON` (`core/parse/inference/responses.go`) reads
  `custom_tool_call_output` the same way as `function_call_output`. Both carry `call_id` and
  `output`. Codex sends the output as `input_text` parts, which `flattenResponsesContent`
  already joins.
- **Praxis:** the Rust port (`praxis/filters/src/parse/inference/responses.rs`) gets the same
  one-line change, per the parity rule.
- **Docs:** the doc comment in `responses.go` and the `ToolResults` field comment in
  `core/model/extensions.go` name the new item type.

The response side already reads Codex's `custom_tool_call` items, so no change is needed there.

Not in this PR: the script plugin's Python SDK (`core/plugins/script/sdk/cortex.py`) and the
CPEX README also name only `function_call_output`. Both are listed in the issue.

## How it was tested

- **New Go test:** `TestInferenceParser_ResponsesRequest_CustomToolResults`
  (`core/plugins/inferenceparser/responses_test.go`), with a body trimmed from a captured Codex
  0.167 request. It fails without the fix and passes with it. The existing
  `TestInferenceParser_ResponsesRequest_ToolResults` still passes.
- **New Rust case** in `go_rules_match_go`. Its expected value is the Go parser's output for the
  same body. It fails without the fix and passes with it.
- **Go:** from `core/`, with `GOWORK=off`: `gofmt -l` is clean, and `go vet` passes for
  `./parse/... ./plugins/inferenceparser/... ./model/...`. `go test` passes for
  `./parse/... ./plugins/... ./model/... ./session/... ./sessionapi/...` except
  `session/archive`'s `TestRecord_NeverBlocksAndCountsDropsWhenFull`. That test fails now and
  then on unmodified `main` too: about 1 in 60 runs on both `main` and this branch.
- **Rust:** from `praxis/`, in a `rust:1.96` container: `cargo fmt --check` and
  `cargo clippy -p cortex-praxis-filters --all-targets -- -D warnings` are clean.
  `cargo test -p cortex-praxis-filters` passes for the `parse::inference::responses` tests and
  the `parity` suite.
- **Live:** the same fix on a branch based on an earlier `main` (before the parser moved to
  `core/parse/inference`). Codex 0.167 was routed through a locally built Cortex. Three sessions
  recorded every tool result: 13 of 13, 12 of 12 and 12 of 12, where they recorded 0 before.
  Git hook rejections now show in the timeline under the commit they rejected.

Assisted-By: Claude (Anthropic AI) <noreply@anthropic.com>
