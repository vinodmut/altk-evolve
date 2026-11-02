# A task-improvement demo for Cortex + Evolve

Task: **"How many activities are done in my current bucket list note?"**

This recreates the SimpleNote example in IBM Research's
[Evolve consistency article](https://huggingface.co/blog/ibm-research/altk-evolve-consistency)
and its [two-minute video](https://www.youtube.com/watch?v=qlp7EzXe8Pg).
It uses a local synthetic notes service, real Claude Code sessions on GPT-4.1,
real Cortex capture/injection, and real Evolve guideline generation. It does not
use an actual SimpleNote account or rerun AppWorld.

The service includes an archived bucket list, the current list, and a shopping
list. Titles and legends contain checkbox symbols that are not activities.
Longer lists expose counting errors even when the agent selects the correct note.

## Observed results

**The 2026-11-02 run did not demonstrate an improvement in task correctness.**
All three arms passed the short lists and failed the long lists.

| Arm | Short lists | Long lists | Total correct | Agent turns |
|---|---:|---:|---:|---:|
| Baseline, no guidance | 3/3 | 0/3 | 3/6 | 28 |
| Standard guidelines | 3/3 | 0/3 | 3/6 | 33 |
| Standard + fast-consistency guideline | 3/3 | 0/3 | 3/6 | 34 |

Cortex recorded actual injection in every guided trial. The guided arms used
more agent turns and reported input tokens (19,252 baseline; 26,472 standard;
28,669 refined), without improving pass/fail results. These are agent CLI usage
figures; they exclude guideline generation and other unreported gateway calls.

| Long-list case | Correct count | Baseline | Standard guidelines | Refined guidelines |
|---|---:|---:|---:|---:|
| `long_a` | 24 | 19 | 20 | 22 |
| `long_b` | 38 | 36 | 35 | 36 |
| `long_c` | 30 | 25 | 26 | 27 |

All these attempts selected the correct current note. None of the long-list
evaluation attempts used a script to compute the count. The counts sometimes
became closer, but every long-list answer remained wrong.

What works in this bounded local demo is capture of real agent turns, automatic
candidate generation from a recorded recovery, reviewed promotion, retrieval,
and Cortex request injection with provenance. What has not worked is getting
the model to apply the learned computation procedure. An automatic outcome-based
promotion gate is also not part of the current plugin: promotion here was a
manual review step.

The complete result table is in [artifacts/summary.json](artifacts/summary.json).
Per-trial `initial.metrics.json` contains the returned answer, independent
expected answer, tool commands, usage, and Cortex injection evidence.

## What Evolve learned

The short training case was already solved correctly. Both standard generation
and a separate fast-consistency attempt returned zero guidelines. The initial
long training attempt also produced no guidelines. After the agent received the
independently checked training answer and was asked to verify it with a small
script, Cortex automatically submitted the recorded recovery to Evolve.

Evolve's standard generator produced these two candidates, copied verbatim into
the isolated reviewed namespace after inspecting their content and provenance:

> When counting formatted items within a note, retrieve the note content using
> the official service API (e.g., 'notes.py read <note_id>') to avoid relying on
> local file state.

> To count the number of completed checklist items, iterate only over lines in
> the note content that strictly match the checklist format (e.g., lines starting
> with '- [x] ').

Both carry `creation_mode: auto-cortex` and a source batch from the training
session. Neither contains a specific note ID or the training answer. Their first
evaluation round did not improve pass/fail accuracy: the agent still counted
the long lists manually. The original candidates are retained in
[artifacts/candidates-standard.json](artifacts/candidates-standard.json).

The full corrected training trace was then read through the Cortex session API
and processed with Evolve's **fast consistency** generator. It produced a third
guideline:

> When extracting specific structured data (such as completed checklist items)
> from a note using an API, always read the note content in its entirety via the
> appropriate API call and process the content programmatically rather than
> relying on contextual clues or partial outputs.

This guideline was reviewed and added verbatim to the other two. Its metadata
records `generation_method: consistency-fast` and the same training session.
See
[artifacts/candidates.json](artifacts/candidates.json) for full provenance.

This demonstrates **learning from a corrected task attempt**. The training
feedback supplied the correct count and asked for script-based verification;
Evolve distilled that demonstrated procedure into guidance for future sessions.
It does not show the agent discovering the procedure without feedback.

## Protocol and limits

- Six matched held-out inputs: three short lists and three long lists. Training
  uses separate note IDs, lengths, and activity flags.
- One baseline, one standard-guideline attempt, and one refined-guideline attempt
  per input. Fresh working directory,
  Claude configuration directory, and session UUID for each attempt. Native
  Claude memory and auto-discovered instructions are disabled with `--bare`.
- The baseline uses an empty reviewed namespace. Training writes only to the
  candidate namespace. Learned content is reviewed before promotion.
- Learning is disabled during evaluation, so held-out sessions do not feed the
  training set. The same task prompt and model are used in both arms.
- The independent grader compares the selected note ID and count against
  structured activity flags. It does not reuse the agent's text-counting method.
- Every Evolve result includes actual Cortex request evidence and guideline IDs.
  Model success alone is insufficient to establish that injection happened.
- The tool-error metric counts Bash results marked as errors. It can miss invalid
  service arguments when a pipeline masks a nonzero exit status. Inspect the
  recorded commands and results for a complete tool-use review.
- Each Claude invocation has a $1 CLI budget cap and a 240-second timeout.
  Generator calls are additional. CLI cost fields have an unknown pricing basis;
  resumed-session cost fields are cumulative. This demo does not establish
  billing savings.

The task was selected adaptively: the first three short baseline cases all
passed, then longer variants were screened. All results are retained. These are
small, selected synthetic cases with one attempt per arm, not an estimate of
general accuracy or repeated-run consistency. The second round was selected
after observing the first round's failures. The source article's resampling
analyzer experiment is a different protocol. This refinement changes both the
generator mode and the training-history window, so it does not isolate which
of those changes caused a difference. No held-out trace was used for mining.

## Walk through a fresh run

Prerequisites: this checkout's `uv` environment, `claude` on PATH, a sibling
Cortex build with `core/plugins/evolve`, an existing trusted Cortex CA, and an
existing Claude sandbox environment file. The defaults use this laptop's
configured paths. `--credentials`, `--cortex-bin`, and `--ca-dir` override them.

From the Evolve repository root, choose a new result directory. The helper below
passes the same directory to every command; it preserves the recorded run here.

```bash
export DEMO_RESULTS=/private/tmp/cortex-learning-demo-fresh
demo() {
  env UV_CACHE_DIR=/private/tmp/rossocortex-uv-cache \
    uv run --no-sync python experiments/cortex_learning_demo/run.py \
    --artifacts-dir "$DEMO_RESULTS" "$@"
}

uv run pytest -v experiments/cortex_learning_demo/test_demo.py
demo preflight
demo start
```

`start` runs a separate Cortex on ports 47800–47804 and Evolve MCP on 18201.
It reuses the trusted CA, disables the cost ledger, and keeps its configuration
and storage inside the selected directory. Check the existing demo has stopped
and those ports are free before starting another run.

Run the six baselines while the reviewed namespace is empty and learning is off:

```bash
for case in a b c long_a long_b long_c; do
  demo trial --label "baseline-$case" --case "$case" --arm baseline
done
```

Train on the separate long case. The feedback command supplies only this
training case's independently correct answer and asks for a reusable procedure.
The verification command asks the agent to check it through the service and a
small script. They resume the training session; evaluation sessions stay fresh.

```bash
demo learning-on
demo trial --label training-long --case long_train --arm training
demo feedback --label training-long
demo verify-training --label training-long
```

After the last response, leave the session idle for 60–90 seconds, plus generation
time. Cortex checks every 30 seconds and requires at least two new agent turns
per batch. Inspect `$DEMO_RESULTS/runtime/cortex.log` for `sent idle session to
learn from`, then read back the actual candidates:

```bash
demo candidates
```

A "sent" log does not guarantee a guideline was generated. Empty output is a
valid result. Review generated content and provenance; accept reusable
procedures, excluding memorized answers or instructions unrelated to the task.
Use the candidate IDs returned by your run, which may differ from the recorded
run's `1` and `2`:

```bash
demo learning-off
demo promote --ids 1 2
demo warm
for case in a b c long_a long_b long_c; do
  demo trial --label "evolve-$case" --case "$case" --arm evolve
done
```

For the refined round, analyze only the training session with fast consistency
generation, review the new candidate, and add it to the reviewed set. In the
recorded run the new candidate ID is `3`; inspect your run's output first.

```bash
demo mine --label training-long --mode consistency
demo candidates
demo promote --ids 3
demo warm
for case in a b c long_a long_b long_c; do
  demo trial --label "consistency-$case" --case "$case" --arm evolve-consistency
done
demo summarize
demo stop
```

`warm` initializes retrieval through the actual MCP server before the first
evaluation request. In each Evolve trial, verify `injected_ids` and the request
events with `injected: true` / `body_rewritten`. Baselines should show
`no_guidelines` and no injected IDs. Inspect the tool commands to see whether the
agent followed the counting procedure, then compare independent pass/fail grades.

`stop` terminates only the process groups this runner recorded. Runtime state
and Claude configuration directories are ignored by Git; metrics and task
transcripts remain available for review. Reuse neither a trial label nor a
results directory for a fresh experiment.

For general setup, canary injection, candidate promotion, withdrawal, and
troubleshooting, see the sibling Cortex checkout's
[integration walkthrough](../../../cortex/docs/evolve-plugin-e2e.md) and
[plugin documentation](../../../cortex/docs/evolve-plugin.md). That plugin uses
stock Evolve MCP; this branch's Cortex REST API is a separate prototype.
