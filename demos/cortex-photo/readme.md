# Cortex + Evolve photo demo

Run three interactive Claude Code sessions in a single Docker container. Cortex records the conversations; full Evolve learns from the GPS session; you review and promote the generated advice; Cortex injects it into a fresh focal-length session. The helper captures the evidence and writes `results/comparison.html`.

You need Docker with Compose, Git, and API access for both Claude Code and Evolve. On a Mac, Docker Desktop or a running Colima instance works. **You do not need host installations of Claude, Cortex, Evolve, Go, uv, Python, or ExifTool.** The image installs them. Both comparison arms get identical tools and the original sample photo.

The full step-by-step page is [walkthrough.html](walkthrough.html). Stay in this demo directory for every `./demo` command.

The [recorded Sonnet 4.5 comparison](reports/sonnet45-20261104/comparison.html) includes the actual guidelines and before/training/after trajectories. Both task and learner used Sonnet 4.5; that pair showed no numeric JSON correctness or tool-call improvement. Its [evidence and regeneration instructions](reports/sonnet45-20261104/readme.md) are committed alongside the report.

The [three fresh Sonnet 4.5 pairs](reports/sonnet45-three-trials-20261105/summary.html) reuse those frozen guidelines. Numeric exports passed in 0/3 baseline attempts and 1/3 guided attempts; total tool calls were 9 → 10. [Pair 3](reports/sonnet45-three-trials-20261105/trial-3/comparison.html) improved correctness and used one fewer tool call, while the first two pairs still failed. The [export readme](reports/sonnet45-three-trials-20261105/readme.md) explains the evidence and offline regeneration.

## 1. Get the bundle in a separate temporary directory

```bash
export DEMO_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/cortex-photo-docker.XXXXXX")"
git clone --depth 1 --single-branch --branch rossocortex \
  https://github.com/vinodmut/kaizen.git "$DEMO_ROOT/kaizen"
cd "$DEMO_ROOT/kaizen/demos/cortex-photo"
cp .env.example .env
```

This downloads the ready-made demo directory. Docker installs Evolve from its pinned Git revision and downloads/builds Cortex from its pinned revision; you do not clone/build Cortex separately. Local development: start here with `cd demos/cortex-photo` in this checkout.

## 2. Configure both LLM connections and start

Edit `.env` and configure these four values. The default connects both Claude Code and Evolve to Anthropic using one API key:

| Value | Purpose | Direct provider example |
| --- | --- | --- |
| `ANTHROPIC_BASE_URL` | Claude's Anthropic-compatible API | `https://api.anthropic.com` |
| `ANTHROPIC_API_KEY` | Claude's API key | Your Anthropic API key |
| `ANTHROPIC_MODEL` | The task model | `claude-sonnet-4-6` |
| `EVOLVE_MODEL_NAME` | Evolve's reflection model | `claude-sonnet-4-6` |

Leave `EVOLVE_CUSTOM_LLM_PROVIDER=anthropic` to use the same Anthropic connection for both models. Evolve uses `ANTHROPIC_BASE_URL` and `ANTHROPIC_API_KEY` directly; Claude Code goes through the local Cortex/bridge path. Keep the same Sonnet task model across baseline, training, and guided runs. Evolve can use that same Sonnet or a stronger available Opus model: change only `EVOLVE_MODEL_NAME` to select the learner. The demo uses API keys; your host's Claude subscription/login is not copied into Docker.

For a gateway serving the Anthropic API, replace the Anthropic URL, key, and both model IDs with its values. To use the same Sonnet in both places, set `ANTHROPIC_MODEL=aws/claude-sonnet-4-5` and `EVOLVE_MODEL_NAME=aws/claude-sonnet-4-5` on a gateway offering that ID. If reflection instead uses the gateway's OpenAI-compatible API, set `EVOLVE_CUSTOM_LLM_PROVIDER=openai`, `OPENAI_BASE_URL=https://YOUR-GATEWAY/v1`, and `OPENAI_API_KEY` to its key. Keep `EVOLVE_MODEL_NAME` set to the gateway's Claude model ID. Here `openai` selects the API format; it does not require using a GPT model.

Use a Claude model for the Claude Code task and keep that model identical across baseline, training, and guided runs. Evolve's reflection model is selected independently. The default is Sonnet 4.6. For an older-model experiment, Sonnet 4.5 is still available until November 30, 2026 on the Anthropic API: use `claude-sonnet-4-5-20250929`, or your gateway's corresponding ID (for example, `aws/claude-sonnet-4-5`). Sonnet 4 and Sonnet 3.7 have already retired on the Anthropic API. Check [Anthropic's model status](https://platform.claude.com/docs/en/about-claude/model-deprecations) before choosing an older model. When changing models, preserve the previous results and start a fresh comparison.

```bash
./demo setup
```

The first build downloads tools, Go dependencies, Python packages, and the small CPU embedding model. Allow several minutes and several GB of disk space. Startup verifies the photo checksum and sends a short real request through Claude/Cortex and a structured tool-call request to Evolve's reflection LLM. Expect `live API OK` for both before starting tasks. A reflection model that only supports ordinary text replies will fail this check before any tasks start. These diagnostic calls have a small API cost and are excluded from task evidence.

Credentials are mounted read-only and excluded from the image. The local CA stays inside the demo; there is no host trust-store change and no published host port. Results live in `./results`.

## 3. Baseline

```bash
./demo baseline
```

Claude starts interactively with the focal-length task already entered. At the folder trust prompt, use the down arrow to select **Yes, I trust this folder**, then Enter (the default is No). Approve local tool calls when prompted; this Claude version can also approve them through its normal auto mode. It should answer and save `photo-result.json`. Enter `/exit` after its final answer. The helper then captures the actual Cortex events and converted trajectory, prints the saved task tags, and reports no guideline injection.

The task requires numeric `FocalLength: 4.38` and numeric `FocalLengthIn35mmFormat: 27` (Pillow calls the latter `FocalLengthIn35mmFilm`). A formatted string such as `"4.4 mm"` fails the numeric-precision requirement. Keep the real baseline even if it already passes.

## 4. GPS training

```bash
./demo training
```

Claude starts a fresh session with “Where was sample.jpg taken?” Accept this folder's trust prompt as above. It should read the GPS tags. The references are N/W and the signed coordinates are approximately `51.4936083333, -0.0375611111`. Correct actual mistakes in this session; do not claim an unverified street address.

After the location answer, paste this follow-up **into Claude Code**:

```text
I also need machine-readable metadata for downstream processing.
Export GPSLatitude and GPSLongitude as signed numeric values to gps-result.json,
using the metadata tool's numeric/unformatted output. Preserve the stored
precision rather than rounding a human-readable display or doing a rounded
conversion. Verify the saved values against the metadata and reference tags.
Show the extraction command, then summarize the verified procedure and any
approaches that actually failed during this session. Keep the summary in
this conversation; do not write a memory file.
```

Expect a numeric GPS export and a verified procedure visible in the conversation. Enter `/exit`; the helper saves the training trajectory. All three runs remain interactive, with ordinary permissions. There is no permission-bypass flag.

## 5. Learn, read the full guidelines, and promote your selection

```bash
./demo learn
./demo promote
```

Learning processes only the recorded GPS UUID and prints full candidate text and provenance. Baseline is excluded. Review reusable procedural advice, such as using a metadata tool's numeric JSON output. Exclude copied coordinates, focal-length answers, invented failures, or scripts available only in training. At the promotion prompt, enter the candidate IDs you selected, separated by spaces. `./demo candidates` prints them again if needed.

Expect unchanged guideline text copied from `photo-candidates` into `photo-reviewed`, with source metadata and a candidate-to-serving ID mapping in `photo-promoted.json`. The selection is frozen for this comparison. Nothing is promoted automatically. If Evolve generates no useful guidelines, keep that outcome; there is nothing to promote.

## 6. Guided retry and comparison

```bash
./demo guided
```

The helper verifies reviewed MCP retrieval and turns injection on. Claude receives exactly the baseline focal-length prompt, in a new session with only the same original photo. Accept the folder trust prompt, approve tool calls when prompted, and enter `/exit` after the final answer. Expect first-request `injected: true` with the promoted serving IDs; inspect the commands and result.

```bash
./demo compare
./demo stop
```

Open `results/comparison.html` in your host browser (on macOS: `open results/comparison.html`). It contains the architecture, identical task prompt, numeric checks, actual outputs, complete generated/reviewed advice, attribution, and expandable before/training/after trajectories. Raw evidence JSON is alongside it. Stopping removes the demo container and network while keeping results and the cached image.

The earlier development pair improved from `"4.4 mm"` to numeric `4.38` after learning the GPS export procedure, with the same tool-call count. **A fresh pair can tie or regress.** The report states the actual outcome; one pair does not establish a reliable average gain.

## Inspect or recover

- `./demo status`: session IDs and progress, without API keys.
- `./demo capture baseline` (or `training`/`guided`): capture a session after a terminal interruption.
- `./demo logs`: supervisor startup errors. Detailed service logs are in `results/runtime/`.
- `./demo check`: rerun both real API checks after correcting `.env`.
- `./demo shell`: inspect the tools/files inside the container.
- For a fresh pair, run `./demo stop`, rename `results` to a unique saved name, and run `./demo setup`. Every task gets a new UUID. A completed arm cannot silently overwrite earlier evidence.

To share evidence, share the report and selected task/guideline JSON files. Keep `.env` and `results/runtime/` private: runtime includes the CA key and Claude configuration. The JPEG is copied unchanged from `demo/workspace/sample.jpg`, SHA-256 `15f46ed3db89d27635c4a1f1c85616bdcac2ae96d3245e53426ee1e7c55d6353`.

Pins: Evolve `161238de15b1d62f55638091c5ed5629b5b46397` on `vinodmut/kaizen:rossocortex`; Cortex `4262d0a84c37589e8d7907cfb70c46f6e08694d4` on `vinodmut/cortex:evolve-plugin`; Claude Code `2.1.287`; Python `3.12.8`; uv `0.9.5`. Python dependencies are locked. ExifTool is the Debian package installed equally for all arms. The gateway bridge preserves text system blocks in order, tool schemas, messages, and streaming; it flattens system text for gateway compatibility.
