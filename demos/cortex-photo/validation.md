# Validation of the Docker photo bundle

The original GPT-4.1 validation and the Sonnet/GPT-4.1 follow-up are retained below. The final section records the matching Sonnet 4.5 experiment; the current local `results/comparison.html` describes that experiment. Earlier reports remain in the archived results directories.

Validated on 2026-11-04 using an Apple Silicon Mac, Colima/Docker, and a gateway serving `Azure/gpt-4.1` through both API formats.

- Built the image from the pinned Git sources, locked dependencies, and pinned Claude Code version. Cortex's full build includes the Evolve plugin; ExifTool 12.57 was available in the container.
- Ran `./demo setup`: container health passed and both real Claude/Cortex and Evolve reflection API probes passed.
- Ran all three tasks through **interactive** Claude Code, accepted each fresh folder's trust dialog, supplied the GPS verification follow-up, and exited each session with `/exit`.
- Learning generated three candidates from only the GPS session. Reviewed and promoted candidate 1, preserving its text and source metadata.
- Read back the guided main request: `injected: true`, serving ID `1`, revision `1`, and GPS source `cortex:9cc21fc4-0e8e-4413-b621-595085a2ae94:40`.
- The baseline and guided attempts both saved numeric focal length `4.38` and equivalent `27`. Both used three assistant messages and two tool calls. **This fresh pair tied on correctness and tool-call count.** The earlier exploratory improvement evidence was not changed.
- Generated `results/comparison.html` and its JSON. Checked local links, desktop/mobile layout, copy buttons, trajectory expansion, and the rendered architecture diagram in Chromium.

Session IDs:

| Arm | UUID |
| --- | --- |
| Baseline | `b218682e-de91-4cae-8a36-ca3067fdf9b3` |
| GPS training | `9cc21fc4-0e8e-4413-b621-595085a2ae94` |
| Guided | `2d712d6d-fe95-4b36-a523-992f35c47ddb` |

The generated advice selected for this run was:

> When extracting GPS metadata for machine-readable downstream processing, use exiftool with the -j (JSON output) and -n (numeric value) flags to directly export signed GPS coordinates. Do not rely on manual conversion via Python or other tools if exiftool provides direct numeric export, as this guarantees full precision and minimizes errors.

Focused automated checks, run from the repository root:

```bash
uv run --no-sync pytest -v tests/unit/test_cortex_photo_demo.py
uv run --no-sync pytest -v -m e2e tests/e2e/test_cortex_photo_bundle.py
uv run --no-sync ruff check demos/cortex-photo tests/unit/test_cortex_photo_demo.py tests/e2e/test_cortex_photo_bundle.py
```

Results: **13 unit tests passed; 1 E2E evidence test passed; Ruff passed.** The demo container and network were stopped after validation, preserving the results and image.

The E2E test independently checks evidence produced by the completed real walkthrough. It skips when those artifacts are absent; it does not synthesize or replay model results. Fresh reports and raw evidence are ignored local files under `results/` and are not bundled as canned results. The test accepts a valid learning/injection loop without requiring a fresh stochastic task to improve.

The native Anthropic/OpenAI endpoint combination and Intel/Linux host execution were not exercised in this run. Docker builds/services and the live gateway path were exercised on Linux ARM64 inside Colima. Elapsed time includes trust dialogs, terminal interaction, and pauses during validation, so it is not used as evidence of a speed gain.

## Sonnet 4.5 follow-up on 2026-11-04

Reran the interactive Docker workflow with task model `aws/claude-sonnet-4-5`, a Claude model available through the configured gateway. Evolve's reflection model stayed `Azure/gpt-4.1`. The original GPT-4.1 results were preserved under the ignored local directory `results/archive-gpt41-20261104T195739Z/` before starting fresh sessions and a fresh guideline store.

| Task comparison | Before | With reviewed guideline | Outcome |
| --- | --- | --- | --- |
| Focal length | 4 tool calls, 5 assistant messages; `FocalLength` saved as string `"4.38"` | 3 tool calls, 4 assistant messages; the same string | Both answers reported the correct value, but both saved JSON with the wrong value type. No numeric JSON correctness gain. |
| GPS task plus identical export/verification follow-up | 9 tool calls; correct signed numeric GPS JSON | 11 tool calls; the same correct signed numeric GPS JSON | Correctness tied; the guided repeat used two more tool calls. |

The GPS comparison is an additional same-task repeat using the GPS session that supplied the learning. It is not a held-out evaluation. Both task prompts, both verification follow-ups, and photo hashes were checked for equality. Numeric GPS values were independently calculated from the photo's raw EXIF rational values with Pillow and checked within `1e-12` degrees. Landmark and neighborhood claims in the answers were not independently verified. The first GPS session's one recorded tool error was `which exiftool exif identify` returning exit code 1 during an availability probe; the numeric export itself succeeded.

Evolve generated two candidates from only the new GPS session:

1. **Promoted unchanged:** When extracting structured GPS data from EXIF metadata for machine use, always use `exiftool` with the `-n` flag and output in JSON format (e.g., `exiftool -n -json -GPSLatitude -GPSLongitude <file>`). This preserves numeric values with full signed decimal precision, suitable for downstream processing.
2. **Retained as a candidate:** Always verify numeric GPS results by comparing extracted values against a manual conversion from Degrees/Minutes/Seconds (DMS) to decimal degrees, and cross-check sign conventions with the reference tags (N/S/E/W).

Candidate 1 was reviewed and promoted before either guided session; candidate 2 was not promoted because its blanket manual-conversion requirement can add unnecessary work. Both guided first requests recorded `injected: true`, serving ID `1`, revision `1`, and source `cortex:01dfd7cf-8d28-457d-81a3-9348111dcc82:28`. Sonnet continued using Python for focal length and manual conversion/sign handling for GPS. These runs do not establish a convincing performance benefit from the guideline.

| Session | UUID |
| --- | --- |
| Sonnet focal baseline | `c3fb6c71-09e4-47e6-aa2e-ba61bdfcebce` |
| Sonnet GPS learning session | `01dfd7cf-8d28-457d-81a3-9348111dcc82` |
| Sonnet guided focal attempt | `a3752f0a-3af8-4ae8-a6a5-9683bdc1a79a` |
| Additional guided GPS repeat | `8c988b15-efa0-4e79-a594-4752d2482ad1` |

Local generated reports: `results/comparison.html` for focal length and `results/gps-comparison.html` for GPS, with computed JSON checks and actual trajectories alongside them. The exploratory GPS launcher and report builder were preserved under ignored `results/`; the primary bundled walkthrough remains the three-session workflow. The existing real-run E2E evidence check passed again (`1 passed`). This check establishes capture, learning provenance, reviewed retrieval, and recorded injection; it does not require task improvement.

The walkthrough now defaults to the supported older Sonnet 4.6 and documents Sonnet 4.5 as an experiment option. Anthropic's published Claude API retirement date for Sonnet 4.5 is November 30, 2026; provider-specific gateway availability can differ. The live requests in this follow-up used the gateway's Sonnet 4.5 ID. No task-model inference ran locally in Docker.

## Matching Sonnet 4.5 task and reflection models

Used `aws/claude-sonnet-4-5` for both interactive Claude Code and Evolve on 2026-11-04. Regenerated guidelines from the unchanged GPS recording `01dfd7cf-8d28-457d-81a3-9348111dcc82`, using a fresh guideline store. The unchanged focal baseline was reused from `results/archive-sonnet45-gpt41-20261104T214015Z/`. The guided attempt was a new live session, `d91ea0ec-a8d3-4788-8b49-e9b40f0ea8c9`, with the same prompt and original JPEG. The learning manifest independently identifies Sonnet 4.5 for both generation and conflict resolution, using the `anthropic` provider and `consistency-fast` generation.

This gateway exposed two response-schema compatibility problems. Anthropic-format generation returned valid guideline JSON but omitted `support`, which LiteLLM's strict schema required despite its default in Evolve's Pydantic model. An identical retry failed again. OpenAI-format reflection then failed because Bedrock rejected the schema's integer `minimum` constraint. Basic API/tool-call probes passed; they did not establish support for the complete guideline schema. No guidelines from these failed attempts were promoted. An earlier Opus 5.5 learning attempt also failed and produced no promoted guidelines.

The successful learning attempt used Evolve's existing unconstrained JSON generation path and local Pydantic guideline validation, still with Sonnet 4.5 through the Anthropic API format. The saved experimental helper `results/sonnet-json-learning.py` overrides only the advertised response-schema capability in that learning process; it does not change Evolve library files, the source recording, task prompts, or generated guideline text. Native response-schema generation on this gateway remains unresolved. The helper and failed-attempt logs are ignored local evidence, and this workaround is explicitly recorded in the generated comparison report. To reproduce this particular learning call inside the running demo container:

```bash
docker compose exec -T demo uv run --offline --no-sync python /results/sonnet-json-learning.py
```

Sonnet generated four candidates:

1. **Promoted unchanged:** When extracting EXIF GPS data for machine-readable output, use the `-n` (numeric) flag with exiftool to obtain signed decimal coordinates directly, rather than parsing human-readable DMS (degrees/minutes/seconds) strings. The `-n` flag automatically applies N/S/E/W reference signs and preserves full precision.
2. **Promoted unchanged:** When a task requires both human-readable presentation and machine-readable export of the same data, perform separate extractions optimized for each purpose rather than attempting to parse human-readable output for machine processing.
3. **Retained as a candidate:** When verifying numeric coordinate conversions, check both the magnitude precision and the sign correctness against reference tags. For GPS coordinates, verify that positive/negative signs match the N/S/E/W reference directions (North=positive lat, South=negative lat, East=positive lon, West=negative lon).
4. **Retained as a candidate:** When checking for available command-line tools, use `which` with multiple tool names in a single command rather than checking each individually. Accept partial availability (some tools present, others missing) as valid if at least one required tool is found.

Codex reviewed the full candidates and provenance and selected 1 and 2 before running guided. Their numeric-output and display/export procedures are supported by the GPS recording and can be relevant to the focal metadata task. Coordinate verification is specific to GPS, and tool-discovery advice was left unpromoted. Candidate content was not edited or supplemented with the focal answer.

| Check | Baseline | Guided |
| --- | --- | --- |
| Saved `FocalLength` | String `"4.38"` | String `"4.38"` |
| Saved 35 mm equivalent | Numeric `27` | Numeric `27` |
| Numeric JSON correctness | Failed | Failed |
| Tool calls | 4 | 4 |
| Assistant messages | 5 | 4 |
| Structured tool errors | 0 | 0 |

**This pair showed no numeric JSON correctness or tool-call improvement.** Both answers reported the correct focal values, but both Python exports converted Pillow's rational focal value to a string. The baseline separately checked numeric precision; guided instead checked ExifTool availability, then still used Python with `json.dump(..., default=str)`. One fewer assistant message does not establish a meaningful efficiency benefit, and elapsed times include terminal approvals and validation pauses.

The guided first main request recorded `injected: true`, serving IDs `1` and `2`, revisions `1`, and source `cortex:01dfd7cf-8d28-457d-81a3-9348111dcc82:28` for both guidelines. The baseline recorded no injection. This verifies recorded plugin attribution; the final outbound API payload was not independently inspected. The generated HTML contains exact outputs, all candidate links, reviewed text, model IDs, reused-recording disclosure, failed learning attempts, and the before/training/after trajectories.

Final checks: **18 unit tests passed; 1 real-run E2E evidence test passed; scoped Ruff formatting/checking and HTML local-link checks passed.** The E2E check verifies the learning/injection loop against real artifacts and does not require improvement. Services were stopped after validation, preserving evidence. The public defaults now select the same Sonnet 4.6 for both roles and support a shared Anthropic API connection; direct Anthropic-hosted Sonnet 4.6 execution was not tested in this gateway experiment.

The committed [matching Sonnet report](reports/sonnet45-20261104/comparison.html) includes all four generated candidates inline and the complete before/training/after trajectories. Selected source JSON files are copied byte for byte, with SHA-256 hashes in `reports/sonnet45-20261104/source-manifest.json`. The [report readme](reports/sonnet45-20261104/readme.md) explains offline regeneration and preserves the learning workaround source. Report packaging validation passed 19 unit tests and the E2E evidence check against the committed export; local links, source hashes, deterministic regeneration, and the Chromium layout were checked.
