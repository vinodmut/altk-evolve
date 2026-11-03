# Cortex trajectory fidelity

Cortex records inference traffic from coding agents. Evolve's adapter reads the
session API, reconstructs conversations, and generates candidate guidelines.
Candidates still require promotion before Cortex can retrieve them.

## What the adapter preserves

| Evidence | Treatment |
| --- | --- |
| User requests and later corrections | Keep full captured text. Render later inputs separately, with message positions, in standard, fast-consistency and accurate-consistency learning prompts. |
| System/developer instructions and `system-reminder` blocks | Keep the original constraints as context, including after compaction. Remove only tagged `evolve-guidelines` blocks to avoid learning from Evolve's own injected output. |
| Tool calls and returned text | Preserve arguments and full text. Keep delayed calls pending across inference turns; match results by ID, including parallel calls. |
| Subagent work | Include captured child transcripts, tool calls/results and definitions as separately labelled observations in sequence order. Child instructions and actions do not become main-agent user input or additional main-agent steps. |
| Outputs crossing a batch boundary | Carry up to ten preceding main turns as supporting context. Render a newly observed result with its earlier call's arguments; explicitly label a result whose action is unavailable. |
| Tool descriptions and schemas | Populate `Trajectory.tools` in OpenAI format and retain manifest changes with their Cortex sequence numbers in context. The standard and fast learning prompts include the available definitions. |
| Compaction and edited histories | Compare message content rather than message counts alone. Guideline injection changes and wire byte counts do not make old user input new. |
| Long logs | The adapter does not truncate user/tool text. Prompt rendering keeps the beginning and ending with explicit omission markers, so an error at the end can remain visible. |
| Long runs | Process every pending turn in consecutive batches of at most 40. Checkpoint each successful batch. Standard and fast Cortex processing reserve up to ten steps for the end of the rendered window when the configured tail is zero. |
| Aborted/incomplete inference | Retain reported finish reasons and HTTP errors. A main request after the last captured response contributes its user inputs and tool outputs without an invented assistant response. |
| Usage and timing | Keep reported model, token counts, presence flags, duration and status in `metadata.cortex_capture`. Render a bounded telemetry excerpt in all three learning prompts. These measurements do not establish task correctness. |
| External evaluation | Accept an explicit `TrajectoryOutcome`. The standard generator uses failed checks/detail. Feedback can revisit an already-ingested session; identical feedback is deduplicated by a stable batch revision. |

New user inputs and boundary observations do not become additional assistant
actions. Existing action numbering, segmentation indices and consistency replay
inputs keep their respective meanings. Supporting context is not another
contribution to learn from.
Adapter observations are explicitly marked and excluded from accurate
consistency resampling, so child internals and diagnostic annotations enrich
extraction without becoming inputs the main agent originally saw.

Prompt budgets still apply. Supporting context and additional input evidence are
each bounded to 20,000 characters; each context message is bounded to 4,000.
Tool definitions and telemetry receive bounded excerpts. The full reconstructed
text remains in the processing input/dry-run output. Accurate consistency keeps
its existing assistant-step scoring/rendering window; the automatic Cortex tail
reservation applies to standard and fast processing.

## Inspect and test

For a session not yet processed by the configured cursor store:

```bash
uv run evolve sync cortex --session SESSION_ID --dry-run
```

For a large backlog, `trajectories` contains every converted batch; `trajectory`
is the first batch for compatibility. Dry-run does not invoke guideline generation
or advance the cursor. An already-processed session reports `up-to-date`.

To inspect an already-processed session with a fresh, isolated cursor, use the
Python adapter with `dry_run=True`:

```python
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from altk_evolve.sync.cortex_sync import CortexCursorStore, CortexSessionClient, ingest_session

with TemporaryDirectory() as directory:
    cortex = CortexSessionClient()
    try:
        result = ingest_session(
            None, cortex, "SESSION_ID", namespace_id="cortex",
            cursors=CortexCursorStore(Path(directory) / "review-cursors.json"),
            min_new_turns=1, dry_run=True,
        )
        print(json.dumps(result.trajectories, indent=2))
    finally:
        cortex.close()
```

Run Python snippets through `uv run python`. Check that the task/rules, expected
tool arguments, user corrections, final error/result and all batch ranges are
present. Check omission markers in the rendered prompt rather than assuming
every byte reaches the LLM.

The regression tests use fake Cortex events and a stubbed LLM. They exercise the
real adapter, prompt generation, HTTP API, processing checkpoints, filesystem
backend, promotion and retirement:

```bash
uv run pytest -v tests/unit/test_cortex_sync.py tests/unit/test_cortex_evidence.py tests/unit/test_cortex_api.py
uv run pytest -v -m e2e tests/e2e/test_cortex_loop.py
```

These checks establish that evidence reaches the learning prompt. They do not
establish a new task-success rate; the saved revenue report describes its original
recorded runs.

## Attach evaluator feedback

Send an authenticated request to the running Evolve Cortex API:

```http
POST /api/cortex/sessions/SESSION_ID/ingest
Authorization: Bearer YOUR_CONFIGURED_TOKEN
Content-Type: application/json

{
  "outcome": {
    "success": false,
    "failed_checks": ["The ledger total must match the reconciled report"],
    "detail": "The generated report omitted refunds."
  }
}
```

A `202` means accepted for background processing. Inspect generated candidate
guidelines or server logs to confirm completion. An outcome reprocesses the
available session in bounded batches even if the cursor is current. Changing the
outcome creates a different revision; repeating identical feedback does not
generate another contribution. The feedback applies to the supplied task's
session; do not apply a single task grade to a session containing unrelated tasks.

Historical candidates are not automatically regenerated when the adapter changes.
Earlier cursor advances that skipped turns are not undone by an ordinary sync;
an isolated dry-run can inspect those turns, and explicit evaluation can replay
the available session.

## What the parsed Cortex API cannot recover

- Images, audio, and other non-text content removed by Cortex's parser.
- Hidden reasoning. Reported reasoning-token counts are measurements, not the text.
- Exact ordering inside a mixed Anthropic content-block array once text and tool
  results have been flattened into separate collections.
- Main-agent history evicted before the oldest available event. The adapter uses
  the first available task and labels unavailable tool actions.
- Tool output never sent back through the proxy, including output from a final
  local execution with no subsequent inference request.
- Tool-less main calls that cannot reliably be separated from title/permission
  side calls by this filter. Child observations are available when Cortex records
  them in this session before a subsequent main-agent turn; separate child sessions
  without a correlation are not joined by guessing.
- Independent ground truth for edits, deployments or other external effects.
  User corrections and tool observations are evidence; evaluator feedback must
  be supplied explicitly when task correctness is known.

The current sibling Cortex parser already reconstructs streamed OpenAI tool-call
fragments. That earlier capture limitation is not a remaining adapter gap.
