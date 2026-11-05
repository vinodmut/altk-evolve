# Three fresh Sonnet 4.5 photo trials

Open [summary.html](summary.html) for all three pairs, the frozen guidelines, session IDs, and links to the complete trajectories. [Pair 3](trial-3/comparison.html) contains the observed improvement. These reports open locally and make no API calls.

| Pair | Numeric JSON: baseline → guided | Tool calls: baseline → guided |
| --- | --- | --- |
| 1 | Fail → Fail | 3 → 4 |
| 2 | Fail → Fail | 2 → 3 |
| 3 | Fail → Pass | 4 → 3 |

Correct numeric exports increased from **0/3 to 1/3**. Total tool calls increased from **9 to 10**; assistant messages decreased from **12 to 11**. Pair 3's guided session used `exiftool -j -n` and saved numeric focal length `4.38` and equivalent `27`. The other five attempts saved focal length as the string `"4.38"`. This is one observed win with inconsistent transfer across repeats; it does not establish an average efficiency gain or isolate causation from model variation.

All six task sessions were fresh interactive Claude Code 2.1.287 sessions using `aws/claude-sonnet-4-5`, with identical prompts, tools, and the original photo. The GPS recording and two Sonnet-generated, reviewed guidelines from the [previous experiment](../sonnet45-20261104/readme.md) were reused unchanged. There was no new learning or corrective coaching. Both reviewed IDs, revisions, and training sources were recorded on every guided first main request; baseline injection was absent. The final outbound payload was not independently inspected. The previous report documents the gateway's learning-schema workaround.

`source-manifest.json` records the original SHA-256 for each exported evidence file or link. Each trial links to the previously committed training, learning, and promotion evidence; photo links resolve to the existing sample. Helpers and this readme are outside the byte-preservation manifest. Private runtime files, credentials, CA material, and Claude configurations are excluded. The [invoice task proposal](next-task.md) is optional and has not been run.

## Regenerate from committed evidence

From the repository root, with the project environment installed:

```bash
uv run --no-sync python demos/cortex-photo/reports/sonnet45-three-trials-20261105/build_summary.py
```

This regenerates the three pair reports, `summary.json`, and `summary.html` without model calls. `evaluate_trials.py` requires native focal tag values to be numeric `4.38` and numeric `27`; it accepts these keys inside JSON wrappers because the prompt did not require a top-level shape. Strings and booleans fail.

`run-trial.py` is the archived interactive driver used inside the prepared Docker environment. It depends on `/opt/demo`, existing services, trial state, and the frozen guideline store; it is not a standalone setup command. Its original comparison command was followed by the wrapper-aware offline evaluator above. Use the [walkthrough](../../walkthrough.html) to prepare a fresh live experiment.

## Check the exported evidence

Run from the repository root:

```bash
for trial in 1 2 3; do
  CORTEX_PHOTO_DEMO_RESULTS="demos/cortex-photo/reports/sonnet45-three-trials-20261105/trial-$trial" \
    uv run --no-sync pytest --noconftest -v -m e2e tests/e2e/test_cortex_photo_bundle.py
done
```

`--noconftest` avoids the unrelated Phoenix startup fixture. These checks use recorded evidence, make no model calls, and verify capture, prompt/photo equality, learning provenance, unchanged promotion, retrieval, and recorded injection. They do not require task improvement. `validation.json` preserves the additional six-session and frozen-guideline checks performed on the original batch.
