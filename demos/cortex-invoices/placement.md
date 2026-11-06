# Guideline placement experiment (2026-11-06)

**Question.** In earlier invoice runs, guided sessions repeated the mistake their guideline
warned about: reading PDFs from an invented `/tmp/tmp.*` path, getting three errors, then
running `pwd`. Was the guideline badly worded, or did the model not act on it where Cortex
put it?

**Answer: placement.** With guidelines at the end of the system prompt, wrong-path reads were
no better than with no guideline at all. With the same guidelines in a `<system-reminder>`
on the first user message, they disappeared, for both a hand-written guideline and the
guideline Evolve learned earlier.

| Arm | Guidelines | Placement | Correct | Runs with wrong-path reads | Median calls | Mean tool errors |
|---|---|---|---:|---:|---:|---:|
| baseline | none | — | 6/6 | 3/6 | 15.5 | 1.5 |
| system | hand-written | end of system prompt | 6/6 | 4/6 | 17 | 2.5 |
| user | hand-written | first user message | 6/6 | **0/6** | 15 | 0.83 |
| learned-system | Evolve's earlier two | end of system prompt | 5/5 | 2/5 | 15 | 2.4 |
| learned-user | Evolve's earlier two | first user message | 5/5 | **0/5** | 14 | 0.2 |

Both placements combined: system-prompt arms 6/11 runs with wrong-path reads, user-message
arms 0/11 (one-sided Fisher exact p ≈ 0.006); user-message arms against baseline 0/11 vs
3/6 (p ≈ 0.03). Small samples, but the direction matches every earlier invoice set, where
guided runs with system-prompt placement repeated the mistake.

## What this changes

- **Evolve's learned guideline was adequate.** "Use absolute paths from the current working
  directory" removed the error once delivered where the model acts on it. The earlier
  diagnosis in [next-steps.md](../next-steps.md) that its wording caused the failure is not
  supported.
- **Cortex should default to `placement: user`** (added in Cortex `ab08dda3`). The block is
  byte-identical on every turn, so prompt caching still hits after the first request.
- **Correctness and call counts are not affected on this task.** Every packet passed in every
  arm. Medians are similar; means are skewed by single long OCR detours (user-1: 50 calls,
  learned-system-1: 49). This task still has no correctness headroom; it shows delivery,
  not a demo-worthy benefit.

## Method

- Model `aws/claude-sonnet-4-5`, Claude Code 2.1.287 in batch mode, Docker runtime built from
  Cortex `evolve-plugin` at `ab08dda3` (`CORTEX_CHECKOUT`), Evolve from this branch's pin.
- Guidelines fixed before any run: the hand-written guideline and the two guidelines promoted
  in [sonnet45-standard-20261105](reports/sonnet45-standard-20261105/summary.html), each in its
  own serving namespace. No learning during the experiment.
- Arms interleaved in rotating order; packets rotate trial-1/2/3 by round. One smoke round of
  three arms (baseline, system, user) preceded five rounds of all five arms.
- Delivery measured at the bridge, i.e. the payload the gateway received, not Cortex's own
  attribution: every guided run had the block in the intended position and none elsewhere;
  no baseline run had it.
- Wrong-path read: a Read call with an absolute path outside the session's task directory.
  Relative reads such as `input/x.pdf` resolve correctly and are not counted.

Evidence: [reports/sonnet45-placement-20261106](reports/sonnet45-placement-20261106/)
(per-run summary, trajectories, bridge delivery log, seeded guidelines).

## Reproduce

```bash
export DEMO_RESULTS_DIR="$PWD/results-placement-manual" CORTEX_CHECKOUT=/path/to/cortex
./demo setup
jq '[.[] | {content, origin: "learned"}]' reports/sonnet45-standard-20261105/invoice-promoted.json \
  > "$DEMO_RESULTS_DIR/learned-guidelines.json"
./demo placement seed
./demo placement run baseline system user learned-system learned-user --count 5
./demo placement-report
./demo stop
```
