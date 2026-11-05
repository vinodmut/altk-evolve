"""Reuse frozen training evidence and report three standard or consistency pairs offline."""

import argparse
import hashlib
import json
import shutil
import statistics
from datetime import UTC, datetime
from pathlib import Path

import evaluate

ROOT = Path(__file__).resolve().parent


def write(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def prepare(results, source):
    """Import only the original training evidence, never evaluation sessions or guides."""
    state = evaluate.load(results / "state.json")
    old = evaluate.load(source / "state.json")
    if state.get("learned") or state["sessions"]:
        raise RuntimeError("Training reuse requires a new, checked results directory.")
    if not state.get("checked_at") or any(state[key] != old[key] for key in ("model", "learning_model")):
        raise RuntimeError("Run setup with the original task and learner models first.")
    training = old["sessions"]["training"]
    recording = evaluate.load(source / "training-trajectory.json")
    if not recording["trajectories"] or any(row["batch"]["conversation_id"] != training["id"] for row in recording["trajectories"]):
        raise RuntimeError("Recorded training identity does not match source state.")
    if not evaluate.load(source / "training-evaluation.json")["all_correct"]:
        raise RuntimeError("The source training outputs were not verified correct.")
    names = ["training-trajectory.json", "training-cortex-session.json", "training-feedback.txt", "training-evaluation.json"]
    if any((results / name).exists() for name in names):
        raise RuntimeError("Training artifacts already exist; evidence is not overwritten.")
    provenance = {
        "reused_at": datetime.now(UTC).isoformat(),
        "source_snapshot": source.name,
        "training_session": training["id"],
        "method": "Byte-identical reuse of recorded training; no new training run or recapture from the new Cortex instance.",
        "sha256": {name: hashlib.sha256((source / name).read_bytes()).hexdigest() for name in names},
        "excluded": "No source evaluation trajectories, outputs, guidelines, or runtime databases are imported for learning.",
        "replay_limit": "Parsed Cortex recordings omit PDF image bytes and hidden reasoning; replay prefixes are approximate.",
    }
    for name in names:
        shutil.copyfile(source / name, results / name)
    state["sessions"]["training"] = {**training, "reused_recording": True}
    state["training_provenance"] = provenance
    write(results / "training-provenance.json", provenance)
    write(results / "state.json", state)
    print("Reused training UUID:", training["id"])


def spread(rows):
    result = evaluate.distribution(rows)
    calls = result["tool_calls"]
    result.update(
        total_tool_calls=sum(calls),
        range_tool_calls=max(calls) - min(calls) if calls else None,
        sample_stddev_tool_calls=statistics.stdev(calls) if len(calls) > 1 else None,
    )
    return result


def analysis_summary(results, config):
    directory = results / "analysis"

    def recorded(prefix):
        path = next(directory.glob(prefix + "*"))
        return evaluate.load(path.with_suffix("") if path.suffix == ".gz" else path)

    ir = recorded("trajectory_ir_")
    score = recorded("consistency_score_card_")
    sampled = [step for step in ir["steps"] if "sampling" in step]
    retained = {step["step_number"]: step["sampling"]["num_samples"] for step in sampled}
    return {
        "replayable_steps": len(ir["steps"]),
        "sampled_steps": len(sampled),
        "requested_samples": len(sampled) * config["max_samples"],
        "retained_samples": sum(retained.values()),
        "threshold": config["high_uncertainty_threshold"],
        "aggregate_trajectory_uncertainty": score["aggregate_trajectory_uncertainty"],
        "high_uncertainty_steps": [
            step["step_number"] for step in score["steps"] if step["step_uncertainty"] > config["high_uncertainty_threshold"]
        ],
        "steps": [{**step, "retained_samples": retained.get(step["step_number"])} for step in score["steps"]],
        "metric_limit": "Tool decisions use Jaccard similarity of function names and arguments. Text uses embedding similarity. Uncertainty scores are not probabilities of an incorrect answer.",
    }


def architecture_svg(mode="consistency", method="accurate"):
    diagram = '<svg viewBox="0 0 1000 180" role="img" aria-label="Recorded training goes from Cortex to Evolve, reviewed advice returns through Cortex"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#0f62fe"/></marker></defs>'
    for x, title, detail in (
        (5, "Invoice PDFs", "Same trial-1 inputs"),
        (205, "Claude Code", "Sonnet 4.5 + tools"),
        (405, "Cortex", "Records calls/results"),
        (605, "Evolve", "Resamples training" if mode == "consistency" and method == "accurate" else "Reflects on training"),
        (805, "Reviewed guides", "Frozen before trials"),
    ):
        diagram += f'<rect x="{x}" y="15" width="185" height="75" rx="8" fill="#e8f0ff" stroke="#0f62fe"/><text x="{x + 92}" y="44" text-anchor="middle" font-size="15">{title}</text><text x="{x + 92}" y="68" text-anchor="middle" font-size="12">{detail}</text>'
        if x < 805:
            diagram += f'<path d="M{x + 185},52 H{x + 198}" stroke="#0f62fe" marker-end="url(#arrow)"/>'
    return (
        diagram
        + '<path d="M898,90 V132 H498 V90" fill="none" stroke="#0f62fe" marker-end="url(#arrow)"/><text x="640" y="164" text-anchor="middle" font-size="13">Cortex injects reviewed advice for fresh guided sessions</text></svg>'
    )


def report(results):
    state = evaluate.load(results / "state.json")
    oracle = evaluate.load(ROOT / "oracle/reference.json")
    promoted = evaluate.load(results / "invoice-promoted.json")
    candidates = evaluate.load(results / "invoice-candidates.json")
    learning = evaluate.load(results / "learning.json")
    config = learning["processing_plan"]["processors"][0]["config"]
    mode = config["guidelines_mode"]
    method = learning.get("consistency_method")
    title = "standard" if mode == "standard" else f"{method} consistency"
    served = {str(row["id"]) for row in promoted}
    pairs = []
    schemas = {}
    for repetition in (1, 2, 3):
        suffix = f"-repeat-{repetition}" if repetition > 1 else ""
        labels = {arm: f"{arm}-1{suffix}" for arm in ("baseline", "guided")}
        if any(not evaluate.completed(state, label) for label in labels.values()):
            raise RuntimeError("All six fresh sessions must be captured before reporting.")
        pair = {"repetition": repetition, **evaluate.comparison(results, state, oracle, served, labels)}
        pairs.append(pair)
        for label in labels.values():
            tools = evaluate.load(results / f"{label}-trajectory.json")["trajectories"][0].get("tools")
            schemas[label] = hashlib.sha256(json.dumps(tools, sort_keys=True).encode()).hexdigest()
    distributions = {arm: spread([pair["metrics"][arm] for pair in pairs]) for arm in ("baseline", "guided")}
    analysis = analysis_summary(results, config["analysis_config"]) if mode == "consistency" and method == "accurate" else None
    summary = {
        "model": state["model"],
        "learning_model": state["learning_model"],
        "guidelines_mode": mode,
        "consistency_method": method,
        "pairs": pairs,
        "distributions": distributions,
        "all_comparisons_valid": all(pair["valid_comparison"] for pair in pairs),
        "same_tool_schemas": len(set(schemas.values())) == 1,
        "tool_schema_sha256": schemas,
        "frozen_selection_unchanged": hashlib.sha256(json.dumps(promoted, sort_keys=True).encode()).hexdigest()
        == state["frozen_guidelines_sha256"],
        "training_provenance": state["training_provenance"],
        "analysis": analysis,
        "limits": "Three pairs on one synthetic packet are exploratory. Call-count stability is separate from output correctness; no causal or population-level claim.",
    }
    write(results / "summary.json", summary)
    before, after = (distributions[arm] for arm in ("baseline", "guided"))
    verdict = (
        f"Correct packets: {before['correct_packets']}/3 baseline → {after['correct_packets']}/3 guided. "
        f"Tool calls: {before['tool_calls']} → {after['tool_calls']}. "
        f"Total: {before['total_tool_calls']} → {after['total_tool_calls']}. "
        f"Median: {before['median_tool_calls']} → {after['median_tool_calls']}; "
        f"range width: {before['range_tool_calls']} → {after['range_tool_calls']}."
    )
    esc = evaluate.esc
    rows, traces = [], []
    for pair in pairs:
        a, b = (pair["metrics"][arm] for arm in ("baseline", "guided"))
        rows.append(
            f"<tr><td>{pair['repetition']}</td><td>{a['all_correct']} → {b['all_correct']}</td>"
            f"<td>{a['items']['exact_rows']}/16 → {b['items']['exact_rows']}/16</td>"
            f"<td>{a['reconciliation']['exact_rows']}/3 → {b['reconciliation']['exact_rows']}/3</td>"
            f"<td>{a['tool_calls']} → {b['tool_calls']}</td><td>{a['observed_tool_errors']} → {b['observed_tool_errors']}</td>"
            f"<td>{pair['valid_comparison']}</td></tr>"
        )
        for arm, label in pair["labels"].items():
            outputs = "".join(
                f"<h4>{name}</h4><pre>{esc((results / 'tasks' / label / name).read_text() if (results / 'tasks' / label / name).exists() else 'Missing')}</pre>"
                for name in ("items.csv", "reconciliation.csv")
            )
            files = " · ".join(
                f'<a href="tasks/{esc(label)}/{esc(path.name)}">{esc(path.name)}</a>'
                for path in sorted((results / "tasks" / label).glob("*"))
                if path.is_file()
            )
            inputs = " · ".join(
                f'<a href="tasks/{esc(label)}/input/{esc(path.name)}">{esc(path.name)}</a>'
                for path in sorted((results / "tasks" / label / "input").glob("*.pdf"))
            )
            traces.append(
                f"<article><h3>{esc(label)} · {esc(state['sessions'][label]['id'])}</h3><p>Source PDFs: {inputs}</p><p>Saved task files: {files}</p>{outputs}"
                f"<details><summary>Independent grading and injection</summary><pre>{esc(json.dumps(pair['metrics'][arm], indent=2))}</pre></details>"
                f'<p><a href="{label}-trajectory.json">Converted trajectory</a> · <a href="{evaluate.artifact_name(results, label + "-cortex-session.json")}">Cortex events</a> · <a href="{label}-cli-1.json">Claude result</a></p>'
                f"{evaluate.trajectory_html(results, label)}</article>"
            )
    selected = {str(row["candidate_id"]) for row in promoted}
    guides = "".join(
        f"<article><h3>Candidate {esc(row['id'])} · {'promoted unchanged' if str(row['id']) in selected else 'not promoted'}</h3>"
        f"<blockquote>{esc(row['content'])}</blockquote><details><summary>Provenance</summary><pre>{esc(json.dumps(row['metadata'], indent=2))}</pre></details></article>"
        for row in candidates
    )
    analysis_links = " · ".join(
        f'<a href="analysis/{esc(path.name)}">{esc(path.name)}</a>' for path in sorted((results / "analysis").glob("*")) if path.is_file()
    )
    notes = evaluate.load(results / "report-notes.json", {})
    interpretation = "".join(f"<p>{esc(line)}</p>" for line in notes.get("interpretation", []))
    if mode == "standard":
        learning_description = "Evolve's standard generator reflects on the original byte-identical, verified training trajectory and its tool calls/results. It does not resample decisions or compute consistency uncertainty. No evaluation recording or feedback is used for learning."
        recording_limit = "Cortex recordings lack PDF image bytes and hidden reasoning. The learner therefore sees parsed text and recorded tool interactions rather than the complete visual context. Fresh CSV grading establishes correctness."
    elif method == "accurate":
        learning_description = "The previous guides used fast consistency generation. This experiment reuses its byte-identical, verified training trajectory with accurate generation: five samples on each of the first 15 replayable steps, default temperature 0.5, high uncertainty threshold 0.15, and the default skip-on-no-uncertainty gate. No evaluation recording or feedback is used for learning."
        recording_limit = "Cortex recordings lack PDF image bytes and hidden reasoning. Resampled prefixes therefore approximate the original decisions where visual input mattered. Resampling observes decision variation; fresh CSV grading establishes correctness."
    else:
        learning_description = "Evolve's fast consistency generator reflects on the original verified training trajectory without decision resampling. No evaluation recording or feedback is used for learning."
        recording_limit = "Cortex recordings lack PDF image bytes and hidden reasoning. Fresh CSV grading establishes correctness."
    analysis_html = (
        f"<details><summary>Resampling counts and uncertainty scores</summary><pre>{esc(json.dumps(analysis, indent=2))}</pre></details>"
        if analysis is not None
        else "<p>No resampling was performed; no analyzer scores were generated.</p>"
    )
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Invoice {esc(title)} experiment</title><style>body{{max-width:1100px;margin:32px auto;padding:0 22px;font:16px/1.6 system-ui;color:#172033;background:#f8fafc;overflow-wrap:anywhere}}article{{background:white;padding:20px;margin:20px 0}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f6;padding:16px;font-size:13px}}blockquote{{border-left:4px solid #0f62fe;padding-left:16px}}table{{border-collapse:collapse;width:100%}}td,th{{text-align:left;padding:10px;border-bottom:1px solid #ccd}}.verdict{{padding:20px;background:#e8f0ff;font-size:20px}}summary{{cursor:pointer}}svg{{width:100%;height:auto}}a{{color:#0043ce}}</style></head><body>
<h1>Invoice extraction with {esc(title)} guidelines</h1><p class="verdict">{esc(verdict)}</p>{interpretation}
<p>Task and Evolve models: <code>{esc(state["model"])}</code> and <code>{esc(state["learning_model"])}</code>. Claude Code 2.1.287, Cortex, Evolve, Poppler and Tesseract run inside Docker; inference uses the remote API.</p>
<h2>Architecture</h2>{architecture_svg(mode, method)}
<h2>Task</h2><p>Three invoices, five PDF pages, 16 items: text extraction, repeated table headings, a description continuing across a page boundary, an image-only second page, signed credits, a three-decimal price and a deliberately mismatched printed total. Every pair and repetition receives the same trial-1 PDF bytes and task text.</p><pre>{esc(state["sessions"]["training"]["prompt"])}</pre>
<h2>Learning and isolation</h2><p>{esc(learning_description)} Selection is frozen before six fresh batch sessions, ordered baseline/guided, guided/baseline, baseline/guided. Each has a fresh UUID, workspace and Claude configuration. Bash, Read, Write and Edit are permitted with no stdin or interactive permission prompts.</p>
<p>{esc(recording_limit)} All tasks share a container/results volume, so filesystem permissions do not isolate sibling workspaces. The author-side oracle is absent from the task container.</p>
<details><summary>Frozen processing configuration</summary><pre>{esc(json.dumps(learning["processing_plan"], indent=2))}</pre></details><p><a href="training-provenance.json">Training hashes and provenance</a> · <a href="learning.json">Learning manifest</a> · <a href="review.json">Selection rationale</a></p><p>{analysis_links}</p>
{analysis_html}
<h2>Generated guidelines</h2>{guides}<p><a href="invoice-promoted.json">Frozen candidate-to-serving mapping</a> · <a href="invoice-candidates.json">All generated candidates</a></p>
<h2>Three fresh pairs</h2><table><thead><tr><th>Pair</th><th>Packet correct</th><th>Item rows</th><th>Reconciliation rows</th><th>Tool calls</th><th>Tool errors</th><th>Valid comparison</th></tr></thead><tbody>{"".join(rows)}</tbody></table><pre>{esc(json.dumps(distributions, indent=2))}</pre>
<p>Range width is maximum minus minimum calls. Sample standard deviation describes these three observations, without estimating a reliable population effect. Correctness requires all 16 complete item rows and all three exact monetary reconciliations; duplicate or extra rows fail. Whitespace and numeric formatting are normalized. All comparisons retain failures.</p>
<details><summary>Input, schema, injection and frozen-selection checks</summary><pre>{esc(json.dumps({key: summary[key] for key in ("all_comparisons_valid", "same_tool_schemas", "frozen_selection_unchanged")}, indent=2))}</pre></details>
<h2>Before and after trajectories</h2>{"".join(traces)}
<h2>Reused training trajectory</h2><p><a href="training-trajectory.json">Converted training</a> · <a href="training-cortex-session.json">Original training events</a></p>{evaluate.trajectory_html(results, "training")}
<h2>Limits</h2><p>{esc(summary["limits"])} Cortex first-request attribution establishes recorded injection; the final outbound payload is not independently inspected. Counted calls are recorded main-agent tool calls, including errors and retries; hidden reasoning is unavailable. The result does not separate advice content from the extra context it adds or compare generation modes on simultaneous identical cohorts.</p><p><a href="summary.json">Computed metrics</a> · <a href="state.json">Session identities and plan</a></p></body></html>"""
    (results / "summary.html").write_text(document)
    print(verdict)
    print("Report:", results / "summary.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "report"))
    parser.add_argument("--results", type=Path, default=Path("/results"))
    parser.add_argument("--source", type=Path, default=ROOT / "reports/sonnet45-20261105")
    args = parser.parse_args()
    if args.command == "prepare":
        prepare(args.results, args.source)
    else:
        report(args.results)


if __name__ == "__main__":
    main()
