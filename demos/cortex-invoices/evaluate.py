"""Evaluate recorded CSVs against author-side references, and build an offline report.

The oracle is mounted only in a separate, network-disabled evaluator container.
It is absent from the task container. No model calls are made here.
"""

import argparse
import collections
import csv
import gzip
import html
import importlib.util
import json
import re
import statistics
from decimal import Decimal, InvalidOperation
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHARED = Path("/opt/demo") if Path("/opt/demo/report.py").exists() else ROOT.parent / "cortex-photo"
SPEC = importlib.util.spec_from_file_location("invoice_shared_report", SHARED / "report.py")
SHARED_REPORT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SHARED_REPORT)
first_request_attribution = SHARED_REPORT.first_request_attribution
messages_for = SHARED_REPORT.messages_for
trajectory_html = SHARED_REPORT.trajectory_html

ITEM_FIELDS = ("invoice_id", "vendor", "description", "quantity", "unit_price", "line_amount")
RECON_FIELDS = ("invoice_id", "subtotal", "tax", "printed_total", "computed_total", "difference", "status")
NUMERIC = {"quantity", "unit_price", "line_amount", "subtotal", "tax", "printed_total", "computed_total", "difference"}


def normalized(field, value):
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    if field not in NUMERIC:
        return value
    value = value.replace(",", "").replace("$", "")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    try:
        number = Decimal(value)
        return number if number.is_finite() else "INVALID_NUMBER"
    except InvalidOperation:
        return "INVALID_NUMBER"


def read_csv(path, fields):
    if not path.is_file():
        return [], [f"Missing {path.name}"]
    try:
        with path.open(newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            errors = [] if tuple(reader.fieldnames or ()) == fields else [f"Wrong columns in {path.name}: {reader.fieldnames}"]
            rows = list(reader)
            if any(None in row for row in rows):
                errors.append(f"Unescaped extra fields in {path.name}")
            return rows, errors
    except (OSError, UnicodeError, csv.Error) as exc:
        return [], [f"Invalid {path.name}: {type(exc).__name__}"]


def compare_rows(expected, actual, fields):
    def key(row):
        return tuple(normalized(field, row.get(field)) for field in fields)

    wanted = collections.Counter(key(row) for row in expected)
    got = collections.Counter(key(row) for row in actual)
    matched = sum((wanted & got).values())
    return {
        "expected_rows": len(expected),
        "actual_rows": len(actual),
        "exact_rows": matched,
        "row_accuracy": matched / max(len(expected), len(actual), 1),
        "missing_or_incorrect": [row for row in expected if wanted[key(row)] > got[key(row)]],
        "unexpected_or_incorrect": [row for row in actual if got[key(row)] > wanted[key(row)]],
        "correct": wanted == got,
    }


def evaluate(directory, invoices):
    expected_items = [
        {field: (invoice[field] if field in ("invoice_id", "vendor") else row[field]) for field in ITEM_FIELDS}
        for invoice in invoices
        for row in invoice["rows"]
    ]
    expected_recon = [{field: invoice[field] for field in RECON_FIELDS} for invoice in invoices]
    items, item_errors = read_csv(directory / "items.csv", ITEM_FIELDS)
    recon, recon_errors = read_csv(directory / "reconciliation.csv", RECON_FIELDS)
    item_result = compare_rows(expected_items, items, ITEM_FIELDS)
    recon_result = compare_rows(expected_recon, recon, RECON_FIELDS)
    return {
        "items": item_result,
        "reconciliation": recon_result,
        "format_errors": item_errors + recon_errors,
        "all_correct": item_result["correct"] and recon_result["correct"] and not item_errors and not recon_errors,
    }


def feedback(results, oracle):
    directory = results / "tasks" / "training"
    score = evaluate(directory, oracle["training"])
    first = results / "training-first-evaluation.json"
    if not first.exists():
        first.write_text(json.dumps(score, indent=2) + "\n")
        archive = results / "training-first-outputs"
        archive.mkdir(exist_ok=True)
        for name in ("items.csv", "reconciliation.csv"):
            if (directory / name).exists():
                (archive / name).write_bytes((directory / name).read_bytes())
    lines = ["I checked your saved CSVs against independently prepared reference data."]
    if score["all_correct"]:
        lines.append(
            "All 16 item rows and all 3 invoice reconciliations match the reference, including complete descriptions, signed credits, and discrepancies."
        )
    else:
        lines.append("The current outputs have these verified issues. Correct the CSVs using the source PDFs and recheck the results:")
        lines.extend(score["format_errors"])
        for name in ("items", "reconciliation"):
            result = score[name]
            if result["missing_or_incorrect"]:
                lines.append(
                    f"Expected {name} rows that are missing or have incorrect fields:\n"
                    + json.dumps(result["missing_or_incorrect"], indent=2)
                )
            if result["unexpected_or_incorrect"]:
                lines.append(
                    f"Current {name} rows that are extra or have incorrect fields:\n"
                    + json.dumps(result["unexpected_or_incorrect"], indent=2)
                )
    lines.append(
        "Summarize the verified extraction and checking procedure, and any approaches that actually failed in this session. Keep this summary in the conversation; do not write a memory file."
    )
    text = "\n\n".join(lines)
    (results / "training-feedback.txt").write_text(text + "\n")
    (results / "training-evaluation.json").write_text(json.dumps(score, indent=2) + "\n")
    print(text)


def load(path, default=None):
    if path.exists():
        return json.loads(path.read_text())
    compressed = path.with_suffix(path.suffix + ".gz")
    if compressed.exists():
        with gzip.open(compressed, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return default


def artifact_name(results, name):
    return name + ".gz" if not (results / name).exists() and (results / (name + ".gz")).exists() else name


def metrics(results, state, label, oracle):
    session = state["sessions"][label]
    score = evaluate(results / "tasks" / label, oracle[session["packet"]])
    messages = messages_for(results, label)
    evidence = load(results / f"{label}-cortex-session.json", {})
    errors = {
        row.get("toolUseId")
        for event in evidence.get("events", [])
        for row in (event.get("inference") or {}).get("toolResults", [])
        if row.get("isError")
    }
    return {
        **score,
        "tool_calls": sum(len(message.get("tool_calls") or []) for message in messages),
        "assistant_messages": sum(message.get("role") == "assistant" for message in messages),
        "observed_tool_errors": len(errors),
        "first_request_evolve": first_request_attribution(evidence),
        "inputs_unchanged": session["input_hashes_before"] == session.get("input_hashes_after"),
        "harness": session.get("harness", {"mode": "interactive"}),
        "harness_transition": bool(session.get("harness_transition")),
    }


def esc(value):
    return html.escape(str(value))


def distribution(rows):
    calls = [row["tool_calls"] for row in rows]
    return {
        "runs": len(rows),
        "correct_packets": sum(row["all_correct"] for row in rows),
        "tool_calls": calls,
        "total_tool_calls": sum(calls),
        "tool_errors": [row["observed_tool_errors"] for row in rows],
        "min_tool_calls": min(calls) if calls else None,
        "max_tool_calls": max(calls) if calls else None,
        "mean_tool_calls": statistics.mean(calls) if calls else None,
        "median_tool_calls": statistics.median(calls) if calls else None,
    }


def comparison(results, state, oracle, served, labels):
    measured = {arm: metrics(results, state, label, oracle) for arm, label in labels.items()}
    before, after = (state["sessions"][labels[arm]] for arm in ("baseline", "guided"))
    attribution = measured["guided"]["first_request_evolve"]
    inputs_valid = bool(
        not measured["baseline"]["first_request_evolve"].get("injected")
        and attribution.get("injected")
        and attribution.get("ids")
        and set(map(str, attribution["ids"])).issubset(served)
        and before["prompt"] == after["prompt"]
        and before["input_hashes_before"] == after["input_hashes_before"]
        and all(row["inputs_unchanged"] for row in measured.values())
    )
    harness_matches = bool(
        measured["baseline"]["harness"] == measured["guided"]["harness"] and not any(row["harness_transition"] for row in measured.values())
    )
    return {
        "labels": labels,
        "input_and_injection_valid": inputs_valid,
        "same_harness": harness_matches,
        "valid_comparison": inputs_valid and harness_matches,
        "metrics": measured,
    }


def completed(state, label):
    session = state["sessions"].get(label, {})
    return session.get("captured") and session.get("exit_code") == 0


def report(results, oracle):
    state = load(results / "state.json")
    promoted = load(results / "invoice-promoted.json", [])
    served = {str(row["id"]) for row in promoted}
    pairs = []
    for number in (1, 2, 3):
        labels = {arm: f"{arm}-{number}" for arm in ("baseline", "guided")}
        if any(not completed(state, label) for label in labels.values()):
            continue
        pairs.append({"trial": number, **comparison(results, state, oracle, served, labels)})
    repeated = []
    repeat_packet = state.get("repetition_plan", {}).get("packet", "trial-1").removeprefix("trial-")
    for repetition in state.get("repetition_plan", {}).get("repetitions", []):
        suffix = f"-repeat-{repetition}" if repetition > 1 else ""
        labels = {arm: f"{arm}-{repeat_packet}{suffix}" for arm in ("baseline", "guided")}
        if all(completed(state, label) for label in labels.values()):
            repeated.append({"repetition": repetition, **comparison(results, state, oracle, served, labels)})
    totals = {
        arm: {
            "correct_packets": sum(pair["metrics"][arm]["all_correct"] for pair in pairs),
            "exact_item_rows": sum(pair["metrics"][arm]["items"]["exact_rows"] for pair in pairs),
            "expected_item_rows": sum(pair["metrics"][arm]["items"]["expected_rows"] for pair in pairs),
            "exact_reconciliations": sum(pair["metrics"][arm]["reconciliation"]["exact_rows"] for pair in pairs),
            "tool_calls": sum(pair["metrics"][arm]["tool_calls"] for pair in pairs),
        }
        for arm in ("baseline", "guided")
    }
    summary = {
        "task_model": state["model"],
        "learning_model": state["learning_model"],
        "completed_pairs": len(pairs),
        "pairs": pairs,
        "totals": totals,
        "same_input_repetitions": repeated,
        "same_input_distributions": {arm: distribution([pair["metrics"][arm] for pair in repeated]) for arm in ("baseline", "guided")},
        "same_input_noninteractive_distributions": {
            arm: distribution(
                [
                    pair["metrics"][arm]
                    for pair in repeated
                    if pair["valid_comparison"] and pair["metrics"][arm]["harness"].get("mode") == "noninteractive"
                ]
            )
            for arm in ("baseline", "guided")
        },
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    batch_distributions = summary["same_input_noninteractive_distributions"]
    batch_before, batch_after = (batch_distributions[arm] for arm in ("baseline", "guided"))
    followup = (
        f"Same-PDF batch follow-up ({batch_before['runs']} new pairs): "
        f"{batch_before['correct_packets']}/{batch_before['runs']} baseline packets correct and "
        f"{batch_after['correct_packets']}/{batch_after['runs']} guided packets correct. "
        f"Calls per run: {batch_before['tool_calls']} → {batch_after['tool_calls']}; "
        f"total calls: {batch_before['total_tool_calls']} → {batch_after['total_tool_calls']}."
        if batch_before["runs"]
        else ""
    )
    rows = []
    traces = []
    for pair in [*pairs, *[pair for pair in repeated if pair["repetition"] > 1]]:
        number = str(pair["trial"]) if "trial" in pair else f"{repeat_packet}-repeat-{pair['repetition']}"
        a, b = (pair["metrics"][arm] for arm in ("baseline", "guided"))
        rows.append(
            f'<tr><td><a href="#pair-{number}">{number}</a></td><td>{a["all_correct"]} → {b["all_correct"]}</td><td>{a["items"]["exact_rows"]}/16 → {b["items"]["exact_rows"]}/16</td><td>{a["reconciliation"]["exact_rows"]}/3 → {b["reconciliation"]["exact_rows"]}/3</td><td>{a["tool_calls"]} → {b["tool_calls"]}</td><td>{a["observed_tool_errors"]} → {b["observed_tool_errors"]}</td><td>{a["harness"].get("mode")} → {b["harness"].get("mode")}{" (transition)" if a["harness_transition"] or b["harness_transition"] else ""}</td><td>{pair["valid_comparison"]}</td></tr>'
        )
        arms = []
        for arm in ("baseline", "guided"):
            label = pair["labels"][arm]
            pdf_links = " · ".join(
                f'<a href="tasks/{esc(label)}/input/{esc(path.name)}">{esc(path.name)}</a>'
                for path in sorted((results / "tasks" / label / "input").glob("*.pdf"))
            )
            outputs = "".join(
                f"<h4>{name}</h4><pre>{esc((results / 'tasks' / label / name).read_text() if (results / 'tasks' / label / name).exists() else 'Missing output')}</pre>"
                for name in ("items.csv", "reconciliation.csv")
            )
            arms.append(
                f'<article><h3>{esc(label)} · {esc(state["sessions"][label]["id"])}</h3><p>Source invoices: {pdf_links}</p>{outputs}<details><summary>Independent evaluation and injection</summary><pre>{esc(json.dumps(pair["metrics"][arm], indent=2))}</pre></details><details><summary>Harness and launch history</summary><pre>{esc(json.dumps(state["sessions"][label], indent=2))}</pre></details><p><a href="{label}-trajectory.json">Trajectory JSON</a> · <a href="{artifact_name(results, label + "-cortex-session.json")}">Cortex events</a></p>{trajectory_html(results, label)}</article>'
            )
        traces.append(f'<section id="pair-{number}"><h2>Pair {number}: outputs and trajectories</h2>{"".join(arms)}</section>')
    selected = {str(row["candidate_id"]) for row in promoted}
    guidelines = "".join(
        f"<article><h3>Candidate {esc(row['id'])} · {'promoted unchanged' if str(row['id']) in selected else 'not promoted'}</h3><blockquote>{esc(row['content'])}</blockquote><details><summary>Provenance</summary><pre>{esc(json.dumps(row['metadata'], indent=2))}</pre></details></article>"
        for row in load(results / "invoice-candidates.json", [])
    )
    a, b = totals["baseline"], totals["guided"]
    verdict = f"Fully correct packets: {a['correct_packets']}/{len(pairs)} baseline → {b['correct_packets']}/{len(pairs)} guided. Exact item rows: {a['exact_item_rows']} → {b['exact_item_rows']}. Tool calls: {a['tool_calls']} → {b['tool_calls']}."
    architecture = '<svg viewBox="0 0 1000 175" role="img" aria-label="PDFs to Claude Code through Cortex; training trajectories to Evolve; reviewed guidelines injected into fresh tasks"><defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#0f62fe"/></marker></defs>'
    for x, title, detail in (
        (5, "Invoice PDFs", "same inputs per pair"),
        (205, "Claude Code", "Sonnet task model"),
        (405, "Cortex", "records tools + results"),
        (605, "Evolve", "learns from training"),
        (805, "Reviewed guidelines", "frozen before trials"),
    ):
        architecture += f'<rect x="{x}" y="15" width="185" height="75" rx="8" fill="#e8f0ff" stroke="#0f62fe"/><text x="{x + 92}" y="45" text-anchor="middle" font-size="15">{title}</text><text x="{x + 92}" y="68" text-anchor="middle" font-size="12">{detail}</text>'
        if x < 805:
            architecture += f'<path d="M{x + 185},52 H{x + 198}" stroke="#0f62fe" marker-end="url(#arrow)"/>'
    architecture += '<path d="M898,90 V138 H298 V90" fill="none" stroke="#0f62fe" marker-end="url(#arrow)"/><text x="550" y="160" text-anchor="middle" font-size="13">Cortex retrieves reviewed advice for each fresh guided session</text></svg>'
    training = state["sessions"].get("training", {})
    notes = load(results / "report-notes.json", {})
    interpretation = "".join(f"<p>{esc(paragraph)}</p>" for paragraph in notes.get("interpretation", []))
    transitioned = [label for label, session in state["sessions"].items() if session.get("harness_transition")]
    transition_note = (
        f"Sessions with a harness change: {', '.join(transitioned)}. Interrupted recordings are preserved, and the same UUID is resumed with a neutral continuation request. Those pairs are excluded from matched-harness claims."
        if transitioned
        else "No session changed harness mid-run."
    )
    document = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Cortex + Evolve invoice trials</title><style>body{{max-width:1100px;margin:32px auto;padding:0 22px;font:16px/1.6 system-ui;color:#172033;background:#f8fafc;overflow-wrap:anywhere}}h1,h2,h3{{line-height:1.25}}article{{background:white;padding:20px;margin:20px 0}}table{{border-collapse:collapse;width:100%}}th,td{{text-align:left;border-bottom:1px solid #ccd;padding:12px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f6;padding:16px;font-size:13px}}blockquote{{border-left:4px solid #0f62fe;padding-left:16px}}.verdict{{background:#e8f0ff;padding:20px;font-size:20px}}summary{{cursor:pointer;padding:8px}}svg{{width:100%;min-width:680px}}.scroll{{overflow:auto}}a{{color:#0043ce}}</style></head><body>
<h1>Invoice extraction: original pairs and same-input repetitions</h1>{f'<p class="verdict">{esc(followup)}</p>' if followup else ""}<p class="verdict">Original three-packet comparison: {esc(verdict)}</p>
<p>Task model: <code>{esc(state["model"])}</code>. Reflection model: <code>{esc(state["learning_model"])}</code>. Claude Code 2.1.287, Cortex, full Evolve, Poppler and Tesseract run in one Docker container; model inference runs through the configured API. The author-side oracle is absent from the task container.</p>
<h2>Architecture</h2><div class="scroll">{architecture}</div>
<h2>Task and inputs</h2><p>Each packet has 16 line items across three synthetic invoices: a text PDF; a two-page text PDF with repeated headings and a description continuing across the page boundary; and a PDF with a text first page and an image-only second page. IDs, vendors, descriptions and amounts differ between training and each evaluation packet. Credits use parentheses; one unit price has three decimal places, with its rounded line amount printed. One invoice per packet has a deliberately mismatched printed total.</p><pre>{esc(training.get("prompt", ""))}</pre>
<h2>Results</h2><div class="scroll"><table><thead><tr><th>Pair</th><th>Entire packet correct</th><th>Exact item rows</th><th>Exact reconciliations</th><th>Tool calls</th><th>Tool errors</th><th>Harness</th><th>Matched comparison</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p>Rows are checked against author-side reference fields, preserving IDs and complete descriptions and comparing decimals exactly. Whitespace and numeric formatting are normalized; duplicate and extra rows count against correctness. Each arm receives identical PDFs, prompt and tools, a fresh UUID, and a separate Claude configuration/workspace. Only training supplies learning and feedback. Reviewed text is frozen before evaluation. Elapsed time includes approvals and interaction and is excluded from efficiency comparisons.</p>
<h2>Repeated identical inputs</h2><p>The original three pairs use different packets. Same-input repetitions, when present, use the original PDFs and prompt in fresh configurations and workspaces. Repetition 1 refers to the original pair and appears in both summaries; do not count it twice. This is exploratory follow-up, not a pre-registered confirmatory study. The table records every session's harness, and the separate noninteractive subset excludes changed-harness pairs.</p><details open><summary>All same-input runs, including the original pair</summary><pre>{esc(json.dumps(summary["same_input_distributions"], indent=2))}</pre></details><details><summary>Noninteractive same-input subset</summary><pre>{esc(json.dumps(summary["same_input_noninteractive_distributions"], indent=2))}</pre></details>
<h2>Harness and interruptions</h2><p>{esc(transition_note)} Noninteractive sessions use --print, --allowedTools Bash,Read,Write,Edit, --permission-mode dontAsk and --permission-prompts none. No stdin is used; unapproved operations are denied. Both arms retain the same task model and tools. The original three-packet totals above include all observed pairs and are descriptive. The per-session launch history records neutral continuation prompts and permission flags.</p>
<h2>Generated guidelines</h2>{guidelines}<p>Learning output mode: {esc(state.get("learning_output_mode"))}. The optional JSON fallback changes only the learning process's schema-capability selection; it retains Sonnet, Evolve prompts and local Pydantic validation.</p>
<h2>Training trajectory · {esc(training.get("id"))}</h2><p><a href="training-trajectory.json">Training JSON</a> · <a href="learning.json">Learning manifest</a> · <a href="invoice-promoted.json">Frozen promotion</a></p><details><summary>Independent training feedback</summary><pre>{esc((results / "training-feedback.txt").read_text() if (results / "training-feedback.txt").exists() else "Not recorded")}</pre></details>{trajectory_html(results, "training")}
{"".join(traces)}<h2>Interpretation</h2>{interpretation}<h2>Interpretation limits</h2><p>These are a small set of paired observations on synthetic invoices, with an exploratory same-input follow-up. They do not estimate performance on arbitrary vendor invoices or isolate causation from ordinary model variation. Cortex's first-request attribution establishes recorded injection; the final outbound payload is not independently inspected. Cortex stores parsed text and tool calls/results; PDF image bytes and hidden reasoning are absent from the converted trajectory. All failed outputs are retained. Tool errors count distinct Cortex isError tool-result IDs; a shell error inside an otherwise successful Bash result may not appear in that count. Each task has a fresh configuration and workspace, but the tasks share a container and results volume; filesystem permissions do not prevent access to another run. The author-side oracle is absent from that container.</p><p><a href="summary.json">Computed metrics</a> · <a href="state.json">Session plan and IDs</a> · <a href="invoice-candidates.json">All candidate metadata</a></p></body></html>"""
    (results / "summary.html").write_text(document)
    print(verdict)
    print("Report:", results / "summary.html")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("feedback", "report"))
    parser.add_argument("--results", type=Path, default=Path("/results") if Path("/evaluation").exists() else ROOT / "results")
    args = parser.parse_args()
    oracle = load(ROOT / "oracle/reference.json")
    {"feedback": feedback, "report": report}[args.command](args.results, oracle)


if __name__ == "__main__":
    main()
