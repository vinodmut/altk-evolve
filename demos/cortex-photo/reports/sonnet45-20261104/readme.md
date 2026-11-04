# Matching Sonnet 4.5 photo experiment

Open [comparison.html](comparison.html) to see the result, task, architecture, all four generated guidelines, the two reviewed copies, first-request attribution, and complete before/training/after converted trajectories. The report contains its styles and diagram; the JSON links resolve to evidence in this directory. It opens locally and makes no API calls.

The baseline and guided attempts both saved `FocalLength` as the string `"4.38"`, with numeric equivalent `27`, and used four tool calls. **This pair showed no numeric JSON correctness or tool-call improvement.** The guided main request recorded both selected guidelines as injected.

Both the task and learner used `aws/claude-sonnet-4-5`. The baseline and GPS recordings were reused unchanged; learning was regenerated in a fresh store, and the guided attempt was a new live session. `source-manifest.json` records SHA-256 hashes of the source files copied byte for byte. The sample photo is already bundled at `../../sample.jpg`; its expected hash is in the manifest. Runtime credentials, CA material, Claude configuration, and prior experiment archives are excluded. Raw failure logs remain local; the saved state and report preserve the errors and successful workaround.

## Rebuild the report from committed evidence

From the repository root, with the project environment installed:

```bash
uv run --no-sync python - <<'REPORT_PY'
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path("demos/cortex-photo").resolve()))
from report import build_report
root = Path("demos/cortex-photo/reports/sonnet45-20261104")
state = json.loads((root / "state.json").read_text())
state.update(json.loads((root / "report-notes.json").read_text()))
build_report(root, state)
REPORT_PY
```

This uses saved evidence and makes no model calls. `report-notes.json` contains the explanatory prose; the copied state and trajectories retain the recorded data.

## Learning workaround used in this run

The gateway's native schema routes failed: Anthropic-format generation omitted `support`, and the OpenAI-format route rejected an integer `minimum` constraint. The successful run used Evolve's existing JSON generation path, retaining local Pydantic validation. The following is the exact process-local helper used for learning; it changes no library source or guideline content:

```python
"""Use Evolve's existing unconstrained JSON path for this gateway experiment.

The gateway fails the native response-schema routes. This leaves generation
prompts, model IDs, source selection and Pydantic guideline validation intact.
Only this learning process overrides the advertised schema capability.
"""
import sys
sys.path.insert(0, "/opt/demo")
import demo
demo.configure_process()
from altk_evolve.llm.guidelines import consistency_guidelines
consistency_guidelines.supports_response_schema = lambda **kwargs: False
demo.learn()
```

For a fresh live run, use the [walkthrough](../../walkthrough.html), keep both model IDs set to Sonnet 4.5 on a gateway where it is available, and complete/capture baseline and GPS training before learning. If reproducing this gateway workaround, save the helper above as `results/sonnet-json-learning.py` in the demo directory, then run:

```bash
cd demos/cortex-photo
docker compose exec -T demo uv run --offline --no-sync python /results/sonnet-json-learning.py
./demo candidates
./demo promote
./demo guided
./demo compare
./demo stop
```

Read the fresh candidates before choosing IDs: stochastic generation can change the content and numbering. The recorded selection of 1 and 2 applies to this saved experiment. Existing completed arms and frozen promotion prevent accidental overwrite; preserve any existing results and start a fresh results directory to retry. Live reproduction uses your private API configuration and incurs API usage.

## Evidence checks

The existing real-run evidence test can inspect this saved export:

```bash
CORTEX_PHOTO_DEMO_RESULTS=demos/cortex-photo/reports/sonnet45-20261104 \
  uv run --no-sync pytest --noconftest -v -m e2e tests/e2e/test_cortex_photo_bundle.py
```

`--noconftest` avoids the unrelated shared Phoenix startup fixture; this test uses only committed evidence. It checks source provenance, prompt equality, tool capture, promotion integrity, retrieval and recorded injection. Passing does not imply that guidelines improved the task.
