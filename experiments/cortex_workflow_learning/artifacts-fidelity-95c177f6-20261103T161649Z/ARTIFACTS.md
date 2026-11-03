# Fresh Cortex/Evolve rerun evidence

Start with [REPORT.html](REPORT.html) for the full comparison, generated
guidelines, architecture, and paired trajectories. The frozen comparison
passed 4/6 with Evolve and 2/6 with baseline; all twelve saved grades were
independently verified.

The baseline Harbor Systems program is preserved verbatim as
[reconcile.py.txt](trials/baseline-ledger_d/reconcile.py.txt), so repository
formatting cannot change the recorded program. Its original command and
contents also remain in the raw trajectory. Generated intermediate JSON,
including invalid reports from failed trials, is retained as evidence.
Trailing whitespace is normalized in the readable HTML and conversation views;
captured input JSON, model-facing prompt JSON, and raw CLI streams are unchanged.

Local runtime storage, session configuration, credentials, and process state
remain excluded by the experiment's existing ignore rules.

To verify saved results without model calls, run from the repository root:

```sh
env UV_CACHE_DIR=/private/tmp/rossocortex-uv-cache uv run --no-sync python -m experiments.cortex_workflow_learning.run --artifacts-dir experiments/cortex_workflow_learning/artifacts-fidelity-95c177f6-20261103T161649Z verify
```
