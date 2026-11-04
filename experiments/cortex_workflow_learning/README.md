# Learning billing workflows with Cortex + Evolve

For an **interactive Claude Code task**, open the
[photo-metadata walkthrough](CLAUDE-CODE-PHOTO.html), adapted from the video
linked on Evolve's website. You enter the GPS training and focal-length prompts
yourself, inspect the captured session and generated guidelines, promote a
reviewed selection, and repeat the focal-length task in a fresh session.
It includes copyable setup commands and a same-task baseline; it makes no claim
that a fresh photo run will necessarily improve.

The [recorded photo development check](photo-live-check.json) learned ExifTool's
numeric JSON flags from a GPS export, then applied them to focal-length
extraction. Baseline saved the formatted `"4.4 mm"`; the guided run saved the
original numeric `4.38`. Both used three agent turns and two tool calls.
The audit includes the guideline, actual saved outputs, tool calls/results,
and first-request injection attribution. This is one pair after task exploration.

For a fresh manual run, open the [HTML end-to-end walkthrough](MANUAL-E2E.html).
It includes copyable commands to clone `vinodmut/kaizen` (`rossocortex`) and
`vinodmut/cortex` (`evolve-plugin`) into a fresh temporary directory, then covers
Evolve LLM configuration and a connection check, the Orchid baseline,
separate Northwind training,
guideline inspection and promotion, a guided rerun, and expected observations
at every step.

The newer [post-merge revenue reconciliation comparison](POSTMERGE.md) includes
the tool-output rendering fix, six fresh cases, a frozen guideline selection,
alternating arm order, and a walkthrough. The export/refund results below are
preserved as earlier evidence.

These examples use real Claude Code / GPT-4.1 calls, real Cortex capture and
request injection, and Evolve's normal guideline generation and retrieval.
The billing service and customer data are synthetic; refunds modify only each
trial's local JSON file.

Two practical tasks are deliberately separate from the Cortex integration tests:

- **Invoice export:** find the production customer, start an asynchronous export,
  poll until ready, and download the complete CSV using the returned artifact ID.
- **Duplicate-charge refunds:** collect all four pages of charges, group captured
  charges by order, preserve each group's oldest capture, and refund only the
  remaining amount on later duplicates. Authorized and reversed charges are
  excluded; one duplicate has already been partially refunded.

The grader checks the actual exported CSV or all resulting charge balances and
refund operations. A convincing answer alone cannot pass.

## A selected positive example

**Refund duplicate captured charges for Willow Works (`refund_f`).** The lesson
learned from a different customer's invoice-export task transferred to this
refund workflow. Both attempts produced the exact correct refunds and preserved
every other charge balance.

| Measure | Baseline | With Evolve |
|---|---:|---:|
| Correct output and service state | Pass | Pass |
| Rejected API calls | 6 | 0 |
| Total agent turns, including help | 16 | 12 |
| Service calls, excluding help | 15 | 7 |

The baseline guessed command names and flags, including `list_orders`,
`--account-id`, and refunds without idempotency keys. The guided attempt read
the documented help for customer lookup, charge listing, and refunds **before**
issuing those calls. It collected every page, preserved the oldest captured
charge per order, and refunded the remaining amounts on the two later duplicates.

Cortex injected only guideline `1`, including on the first request. Its source
is the Northwind Studio export training session. The complete before/after
commands, grades, answers, and first-request evidence are in
[positive-refund-example.json](artifacts/positive-refund-example.json).

This case was selected after seeing the results. **The full six-case comparison
did not show an overall improvement:** baseline passed 6/6 using 86 turns;
Evolve passed 5/6 using 87 turns. The export for Summit Labs stopped after an
unsuccessful customer-name search, and two refunds used more turns. The positive
case demonstrates the useful behavior when the agent applies the lesson; it
does not establish a reliable average gain.

| Task and customer | Correct: baseline / Evolve | Rejected calls: baseline / Evolve | Turns: baseline / Evolve |
|---|---|---:|---:|
| Export: Harbor Systems | Pass / Pass | 5 / 4 | 15 / 12 |
| Export: Summit Labs | Pass / Fail | 6 / 3 | 15 / 7 |
| Export: Willow Works | Pass / Pass | 6 / 2 | 13 / 10 |
| Refund: Harbor Systems | Pass / Pass | 5 / 7 | 14 / 32 |
| Refund: Summit Labs | Pass / Pass | 4 / 6 | 13 / 14 |
| Refund: Willow Works | Pass / Pass | 6 / 0 | 16 / 12 |

See [summary.json](artifacts/summary.json) for every arm and initial screening
attempt. A sandbox-blocked call with zero model tokens, zero service calls, and
no Cortex session was retried; it is documented separately in
[excluded-network-attempts.json](artifacts/excluded-network-attempts.json).

## The learned instruction

Evolve generated this instruction from the separate Northwind Studio export
training session; it is served verbatim:

> Consult command-specific help before issuing API commands, especially when
> encountering unfamiliar or ambiguous arguments.

It contains neither a customer ID nor a refund answer. Testing it on refunds
checks transfer of an export-derived procedure to a different task family.
Only candidate `1` was promoted, before any guided evaluation. Other candidates
remain available for inspection but are not served.

The export training task was correct but contained five rejected API calls.
Correctness-only standard processing generated zero guidelines. We then supplied
a distinct quality objective: complete correctly **without rejected API calls**.
The recorded outcome explicitly preserves successful task correctness and marks
this execution-quality criterion as failed. Evolve generated the instruction
above from the actual Cortex trajectory plus that feedback. This is learning
with measured feedback, not a claim that the successful export failed.

A later fast-consistency analysis of the same training session produced another
help-related candidate. It was not promoted. The first consistency submission
was a redelivery and was skipped; a new analysis revision ran the processor.
No evaluation trajectory was used for learning.

## A gateway compatibility problem

The configured GPT-4.1 gateway ignores later text blocks in an Anthropic `system`
array. A two-request diagnostic gave `FIRST_BLOCK` for an array, but the exact
random marker from the second block when the same text was sent as a string.
See [the probe](artifacts/system-block-probe.json).

Cortex appends Evolve instructions as a new system text block. Consequently,
`injected: true` and `body_rewritten` alone did not establish that this gateway
delivered the instruction to the model. The initial direct-gateway experiments
are retained and must not be presented as a valid model-visible guideline
comparison. Exports passed 3/3 in both arms; refunds passed 2/3 without guidance
and 1/3 in the nominal guided arm.

The included [bridge](bridge.py) runs **after Cortex** and converts all system
text blocks into one string, preserving their order and text. Messages, tools,
and streamed responses pass through. Both comparison arms use this same bridge.
A separate real Claude Code probe quoted the exact Evolve instruction, with
matching Cortex source/injection evidence in
[the readback](artifacts/bridge-probe-escalated/evidence.json).

This is a local gateway workaround. The ordinary Cortex binary and everyday
configuration are unchanged. Flattening removes system-block cache metadata;
these runs do not establish prompt-cache performance or billing savings.

## Walk through a fresh comparison

Prerequisites are this checkout's `uv` environment, `claude` on PATH, the sibling
`../cortex/bin/cortex` build with Evolve, an existing trusted Cortex CA at
`~/.cortex/ca`, and the existing Claude sandbox environment file used by the
driver. Credentials are read into subprocess environments and are never written
to trial files. The defaults are specific to this development checkout.

From the repository root, choose a new artifact directory. Each run creates
fresh working directories, Claude configuration directories, and session UUIDs.
Native memory and auto-discovered instructions are disabled with `--bare`.

```bash
export WORKFLOW_RESULTS=/private/tmp/cortex-workflow-fresh
workflow() {
  env UV_CACHE_DIR=/private/tmp/rossocortex-uv-cache \
    uv run --no-sync python -m experiments.cortex_workflow_learning.run \
    --artifacts-dir "$WORKFLOW_RESULTS" "$@"
}

uv run pytest -v experiments/cortex_workflow_learning/test_workflows.py
workflow preflight
workflow start
workflow bridge-start
workflow injection-off

for family in export refund; do
  for variant in d e f; do
    case="${family}_${variant}"
    workflow trial --label "baseline-$case" --case "$case" --arm baseline --via-bridge
  done
done
```

The isolated Cortex uses ports 47800–47804, Evolve MCP uses 18201, and the bridge
uses 18202. Those ports must be free. Automatic learning stays disabled during
the entire comparison; baselines explicitly disable request injection.

Run the separate training task, then generate and inspect candidates:

```bash
workflow trial --label train-export --case export_train --arm training --via-bridge
workflow learn --label train-export --objective fewer-errors
workflow candidates
```

Generation is data-dependent: an empty list is valid, especially if the fresh
training agent makes no mistakes. Inspect content and provenance, and select a
reusable procedure before starting guided evaluation. Use the ID returned by
your run; the recorded instruction above was candidate `1`.

```bash
workflow promote --ids 1
workflow warm
workflow injection-on

for family in export refund; do
  for variant in d e f; do
    case="${family}_${variant}"
    workflow trial --label "evolve-$case" --case "$case" --arm evolve --via-bridge
  done
done

workflow summary
workflow stop
```

Inspect `summary.json`, each trial's `initial.metrics.json`, and
`service.calls.jsonl`. Guided trials should have the selected `injected_ids`
and actual Cortex request rewrites; baselines should have none. Compare
independent `passed`/`checks`, rejected service calls, and `num_turns`.
Help-only calls are excluded from service-call counts, but included in total
agent turns. Unknown-command help attempts count as rejected calls.

Each Claude invocation has a $1 CLI cap and a 240-second timeout. Generation and
retrieval calls are additional. `stop` terminates only the process groups recorded
by this experiment, including its bridge, and retains the artifacts.

## Interpretation

Training uses Northwind Studio. The first screening round used Maple Workshop,
Orchid Analytics, and Cedar Design. The comparison through the bridge uses
previously unused Harbor Systems, Summit Labs, and Willow Works inputs, with
different IDs, amounts, and invoice counts. The served instruction was not
changed after screening.

These are small, adaptively selected synthetic examples with one attempt per
arm and input. Baselines run before guided trials. Report correctness and
efficiency separately; do not infer statistically established general gains or
cost savings. Full results, including failed and ineffective attempts, are
retained in [artifacts](artifacts/).
