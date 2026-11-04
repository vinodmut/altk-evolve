# Validation of the Docker photo bundle

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
