# Recorded Sonnet 4.5 invoice experiment

Open summary.html for the complete task, generated guidelines, outputs and trajectories.
This snapshot includes one training session, the original three different-packet pairs,
and two additional pairs on identical packet-1 PDFs. Training alone supplied learning.
The reviewed selection stayed frozen. All model IDs are aws/claude-sonnet-4-5.

The first sessions were interactive. Baseline 2 switched to batch mode within the same
UUID; its interrupted capture and neutral continuation are preserved. Baseline 3,
guided 3, and the additional pairs ran with explicit tool approval and no stdin.
The report separates the changed-harness pair and the noninteractive same-input subset.

Regenerate the report from the repository root without model calls:

    uv run --no-sync python demos/cortex-invoices/evaluate.py report --results demos/cortex-invoices/reports/sonnet45-20261105

The author-side references remain in the demo's oracle directory. No credentials,
runtime configurations, CA material or live database are included here.
source-manifest.json records SHA-256 hashes; the live results directory remains ignored.

Agent-generated Python scripts are retained as .py.txt files with unchanged contents.
Their original filenames and commands are in the trajectories and source manifest.
This keeps the recorded scripts as data and protects them from source formatters.

The two largest raw Cortex event files use lossless .json.gz compression.
The readable trajectories and HTML report remain uncompressed.
