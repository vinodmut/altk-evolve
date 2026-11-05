# Cortex + Evolve invoice demo

Extract invoice PDFs into checked CSVs in Claude Code, let Evolve learn from one training session, then run fresh invoices with reviewed guidelines. Cortex, Evolve, Claude Code, Poppler, and Tesseract run inside Docker. The models use your configured API. You need Docker with Compose, Git, and API access; host PDF tools, Python, uv, and Claude are not required.

Each packet contains three synthetic invoices and 16 line items: a text invoice; a multipage invoice with repeated headings and a description continuing across a page boundary; and an invoice whose second page is scanned. They include signed credits, fractional quantities, a three-decimal unit price with a printed rounded line amount, and one mismatched printed total. Training and all three evaluation packets use different IDs, vendors, descriptions, and amounts.

The task is printed in [prompt.txt](prompt.txt). It asks for `items.csv` and `reconciliation.csv`; the exact field definitions are part of the prompt. The [sample training invoices](fixtures/training/) and other packets are ready to use. Author-side references live in `oracle/` and are excluded from the task image. A separate evaluator container, with networking disabled, reads the references and output files. The task agent cannot see that container or its mounts.

The [recorded Sonnet 4.5 report](reports/sonnet45-20261105/summary.html) includes the original three pairs and a same-input follow-up. The original pairs all passed, with 52 baseline tool calls versus 53 guided calls; they did not show an overall improvement. Two further batch pairs on identical PDFs used 36 and 68 baseline calls versus 12 and 16 guided calls. The 68-call baseline lost a description continuation; both guided runs passed. This is a small observed benefit with substantial baseline variation. The report records the interactive-to-batch switch and separates comparisons with matching harnesses. The commands below use batch mode consistently for new runs.

## 1. Get the bundle and configure both model connections

```bash
export DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/cortex-invoices.XXXXXX")"
git clone --depth 1 --single-branch --branch rossocortex \
  https://github.com/vinodmut/kaizen.git "$DEMO_ROOT/kaizen"
cd "$DEMO_ROOT/kaizen/demos/cortex-invoices"
cp ../cortex-photo/.env.example .env
```

For local development, use `cd demos/cortex-invoices` in this checkout. Stay in this directory for every `./demo` command. The new bundle must be present in the branch you clone.

Edit `.env`. Set `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, and `EVOLVE_MODEL_NAME`; keep `EVOLVE_CUSTOM_LLM_PROVIDER=anthropic` for a shared Anthropic connection. Direct-provider defaults select Sonnet 4.6 for both task and learner. The recorded experiment uses the gateway's `aws/claude-sonnet-4-5` ID for both. Choose model IDs your endpoint serves, and keep them unchanged during a comparison. Your host's Claude subscription/login is not copied into Docker.

```bash
./demo setup
```

The launcher builds the shared pinned runtime from `../cortex-photo`, adds the PDF tools, starts services on container loopback, and makes two small live API checks. Expect `live API OK` for Claude and Evolve. No host port or host trust-store change is needed. The first build takes several minutes; subsequent builds reuse cached layers.

## 2. Complete one training packet and inspect real feedback

```bash
./demo training --batch
```

Claude runs the supplied task and exits automatically. Bash, Read, Write, and Edit are explicitly allowed; other operations that need permission are denied. No stdin, folder trust answer, or `/exit` is required. Expect a captured training UUID and tool-call count, with guideline injection off. The final Claude result is saved as `results/training-cli-1.json`; Cortex records the intermediate tool calls and results.

```bash
./demo feedback
./demo resume training --batch
./demo feedback
```

The evaluator prints reference-based feedback and saves it to `results/training-feedback.txt`. The batch resume supplies that text automatically. It requests corrections only for actual output errors, followed by a summary of the verified procedure and real failed approaches. If the first outputs pass, it confirms that outcome and requests the summary. The final feedback command checks the saved outputs again. The original outputs and first evaluation are preserved alongside the final training recording.

## 3. Learn, review the generated text, and promote

```bash
./demo learn --json-output
./demo candidates
```

Learning processes only the training UUID. Expect full generated guideline text and the source session ID; complete provenance is saved in invoice-candidates.json. The command uses Evolve's existing JSON generation with local Pydantic validation, which also works with the gateway used in the recorded experiment. Native response-schema generation was incompatible with that gateway. The flag changes no library source; omit it only if you want to test native schema support on your endpoint.

Review the candidates. Select reusable, evidenced procedures; leave invoice-specific answers, invented failures, and unjustified advice unpromoted. Then run:

```bash
./demo promote
```

Enter your selected candidate IDs when prompted. Expect unchanged copies in `invoice-reviewed`, with a saved candidate-to-serving ID mapping. The selection is frozen before the trials. There is no automatic promotion and no new learning from evaluation cases.

## 4. Run three fresh baseline/guided pairs

```bash
./demo baseline 1 --batch
./demo guided 1 --batch
./demo guided 2 --batch
./demo baseline 2 --batch
./demo baseline 3 --batch
./demo guided 3 --batch
```

Each session finishes automatically and saves its CLI JSON plus Cortex trajectory. Both arms receive the identical prompt, original PDFs, and tools in fresh workspaces and Claude configurations. Baseline injection is off even when reviewed guidelines exist. Guided sessions preview MCP retrieval, enable injection, and capture the first request's serving IDs. No previous scripts or output CSVs are copied into a fresh task workspace. The alternating order reduces a simple run-order bias; six sessions are still a small experiment.

The three pairs use three different invoice packets. To measure variation on identical inputs, optionally repeat packet 1 in fresh sessions:

```bash
./demo baseline 1 --repeat 2 --batch
./demo guided 1 --repeat 2 --batch
./demo guided 1 --repeat 3 --batch
./demo baseline 1 --repeat 3 --batch
```

Repeat numbers preserve the earlier UUIDs, recordings and outputs. All three repetitions use identical PDF bytes and task text, but separate workspaces and configurations. The report shows these separately from the different-packet comparison; the original pair 1 appears in both summaries and must not be counted twice. Keep the model, harness, and frozen guidelines unchanged.

## 5. Read the actual results and stop services

```bash
./demo report
./demo stop
```

Open `results/summary.html` in your host browser. It includes the architecture, task, all generated guidelines, training feedback, before/after trajectories, exact CSV outputs, reference-based checks, tool calls, harness modes, and injection attribution. It reports ties and regressions as well as improvements. Correctness is the primary measure; fewer tool calls are useful when correctness is preserved. Terminal interaction and harness changes make elapsed time unsuitable as a speed comparison.

Raw Cortex events, converted trajectories, and outputs are saved beside the report. `.env` and `results/runtime/` are private and ignored; runtime includes credentials/configuration and CA material. `./demo capture baseline-1` can recover evidence after a terminal interruption. A completed arm cannot overwrite its earlier evidence. For a new experiment, stop services and rename `results` before setup.

To use Claude Code interactively, omit `--batch` consistently from both arms. Accept the folder trust prompt, approve local tools, and enter `/exit` after the answer. For interactive training feedback, paste `results/training-feedback.txt` into `./demo resume training`. If an evaluation session is interrupted, `./demo resume baseline 2 --batch` resumes that UUID with only a neutral request to finish the original task; the report flags a changed harness. Completed evaluation sessions and learned training cannot be resumed by the driver.

Fixture maintenance is optional and requires uv: `uv run --project demos/cortex-invoices python demos/cortex-invoices/build_fixtures.py` from the repository root. It regenerates PDFs from the author-side catalog and writes reference data and SHA-256 hashes. Normal walkthrough users use the bundled PDFs. The Docker task environment uses the shared locked runtime; this directory's `pyproject.toml` and `uv.lock` are only for fixture generation.
