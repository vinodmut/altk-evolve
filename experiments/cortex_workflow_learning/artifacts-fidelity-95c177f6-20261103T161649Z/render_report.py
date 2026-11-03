"""Render this frozen experiment's saved evidence; no model or network calls."""

import html
import json
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def esc(value):
    return html.escape(str(value))


def pretty(value):
    return esc(json.dumps(value, indent=2, ensure_ascii=True))


def transcript(label):
    blocks = []
    for line in (ROOT / "trials" / label / "initial.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record.get("type") not in ("assistant", "user"):
            continue
        content = record.get("message", {}).get("content", [])
        if isinstance(content, str):
            content = [{"type": "text", "text": content}]
        for block in content:
            kind = block.get("type")
            if kind == "text":
                text = block.get("text", "")
                title = "Agent" if record["type"] == "assistant" else "User"
            elif kind == "tool_use":
                title = f"Tool call: {block.get('name', '')}"
                text = json.dumps(block.get("input", {}), indent=2)
            elif kind == "tool_result":
                title = "Tool result" + (" (error)" if block.get("is_error") else "")
                text = block.get("content", "")
                if not isinstance(text, str):
                    text = json.dumps(text, indent=2)
            else:
                continue
            blocks.append(f"<div class='event'><strong>{esc(title)}</strong><pre>{esc(text)}</pre></div>")
    return "".join(blocks)


def outcome(trial):
    return "Timeout" if trial.get("timed_out") else "Pass" if trial["passed"] else "Fail"


def main():
    state, plan, verification = read("state.json"), read("comparison-plan.json"), read("verification.json")
    trials = {trial["label"]: trial for trial in state["trials"] if trial["arm"] in ("baseline", "evolve")}
    assert len(trials) == 12 and len(verification) == 12 and all(item["matches_saved_grade"] for item in verification)
    totals = {
        arm: {
            "passes": sum(trial["passed"] for trial in trials.values() if trial["arm"] == arm),
            "turns": sum(trial["num_turns"] for trial in trials.values() if trial["arm"] == arm),
            "rejected": sum(trial["service_errors"] for trial in trials.values() if trial["arm"] == arm),
            "timeouts": sum(trial.get("timed_out", False) for trial in trials.values() if trial["arm"] == arm),
        }
        for arm in ("baseline", "evolve")
    }
    wins, regressions, rows, case_details = [], [], [], []
    all_injection_correct = True
    for case in plan["cases"]:
        baseline, evolved = trials[f"baseline-{case}"], trials[f"evolve-{case}"]
        fixture = read(f"trials/baseline-{case}/service-state.json")
        name = next(customer["name"] for customer in fixture["customers"] if customer["environment"] == "production")
        month = fixture["ledger"][0]["occurred_at"][:7]
        task_prompt = (
            f"Reconcile {month} revenue for {name}'s production account according to the billing service's accounting contract. "
            "Use the local service via `python3 ops.py`; its --help describes the API. Save report.json with totals_minor "
            "(currency to integer minor-unit net revenue) and included_event_ids (sorted distinct IDs). "
            "Return the same JSON in your final answer."
        )
        if not baseline["passed"] and evolved["passed"]:
            wins.append(name)
        if baseline["passed"] and not evolved["passed"]:
            regressions.append(name)
        expected_ids = {guideline["id"] for guideline in plan["guidelines"]}
        for trial in (baseline, evolved):
            first_ids = set((trial["evidence"][0].get("evolve") or {}).get("ids", [])) if trial["evidence"] else set()
            all_injection_correct &= first_ids == (expected_ids if trial["arm"] == "evolve" else set())
        rows.append(
            f"<tr><td>{esc(name)}</td><td class='{outcome(baseline).lower()}'>{outcome(baseline)}</td>"
            f"<td class='{outcome(evolved).lower()}'>{outcome(evolved)}</td><td>{baseline['num_turns']} / {evolved['num_turns']}</td>"
            f"<td>{baseline['service_errors']} / {evolved['service_errors']}</td>"
            f"<td>{baseline['duration_seconds']:.1f} / {evolved['duration_seconds']:.1f}</td></tr>"
        )
        arm_details = []
        for trial in (baseline, evolved):
            label = trial["label"]
            arm_details.append(
                f"<section><h3>{esc(trial['arm'].title())}: {outcome(trial)}</h3>"
                f"<p>{trial['num_turns']} turns; {trial['service_errors']} rejected service calls; "
                f"{trial['duration_seconds']:.1f} seconds. Session <code>{esc(trial['session_id'])}</code>.</p>"
                f"<p>Checks: <code>{esc(json.dumps(trial['checks']))}</code></p>"
                f"<p><a href='trials/{esc(label)}/initial.metrics.json'>Metrics and injection evidence</a> · "
                f"<a href='trials/{esc(label)}/initial.jsonl'>Raw trajectory</a></p>"
                f"<h4>Returned answer</h4><pre>{pretty(trial['answer'])}</pre>"
                f"<details><summary>Complete recorded conversation and tool actions</summary>{transcript(label)}</details></section>"
            )
        case_details.append(
            f"<details class='case'><summary>{esc(name)}: baseline {outcome(baseline).lower()} → Evolve {outcome(evolved).lower()}</summary>"
            f"<h3>Task prompt for both arms</h3><pre>{esc(task_prompt)}</pre>"
            f"<h3>Independent expected answer</h3><pre>{pretty(baseline['expected'])}</pre>"
            f"<div class='pair'>{''.join(arm_details)}</div></details>"
        )
    candidates = {item["id"]: item for item in read("candidates.json")}
    guideline_html = []
    for item in state["promoted"]:
        source = candidates[item["candidate_id"]]
        # JSON escaping makes generated control characters visible without altering the stored guideline.
        guideline_html.append(
            f"<article><h3>Serving guideline {esc(item['id'])} · candidate {esc(item['candidate_id'])}</h3>"
            f"<pre>{pretty(source['content'])}</pre><p>{esc(source['metadata']['rationale'])}</p>"
            f"<p>Generated by <code>{esc(source['metadata']['generation_method'])}</code> from training session "
            f"<code>{esc(source['metadata']['session_id'])}</code>.</p></article>"
        )
    audit = read("recording-audit.json")
    facts = {
        "totals": totals,
        "paired_wins": wins,
        "paired_regressions": regressions,
        "first_request_injection_matches_arm": all_injection_correct,
        "all_saved_grades_reverified": True,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    (ROOT / "result-facts.json").write_text(json.dumps(facts, indent=2) + "\n")
    assert all_injection_correct, "First-request injection does not match the frozen comparison"
    verdict = (
        "Evolve improved exact task completion in this rerun."
        if totals["evolve"]["passes"] > totals["baseline"]["passes"]
        else "Evolve did not improve exact task completion in this rerun."
        if totals["evolve"]["passes"] == totals["baseline"]["passes"]
        else "Evolve reduced exact task completion in this rerun."
    )
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Cortex + Evolve: fresh revenue reconciliation rerun</title>
<style>
:root{{font-family:system-ui,sans-serif;color:#162335;background:#f3f5f8;line-height:1.6}}body{{margin:0}}main{{max-width:1160px;margin:auto;padding:32px 22px 70px}}h1{{font-size:2.2rem;line-height:1.2}}h2{{margin-top:42px}}h3{{line-height:1.4}}.hero,article,.case{{background:white;padding:22px;border:1px solid #d9e0e9;border-radius:12px;margin:16px 0}}.lead{{font-size:1.2rem}}.stats,.pair,.flow{{display:flex;gap:16px;flex-wrap:wrap}}.stat{{background:#e9efff;padding:16px 24px;border-radius:8px;flex:1}}.stat b{{font-size:1.7rem;display:block}}.pair>section{{flex:1;min-width:280px;overflow:hidden}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{text-align:left;padding:12px;border-bottom:1px solid #e1e5ed}}th{{background:#e9efff}}.pass{{color:#167043;font-weight:bold}}.fail,.timeout{{color:#a32925;font-weight:bold}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;max-height:650px;overflow:auto;background:#f3f5f8;padding:14px;font-size:.83rem;line-height:1.5}}code{{overflow-wrap:anywhere}}summary{{cursor:pointer;font-weight:600}}.event{{border-left:3px solid #becdea;padding-left:12px;margin:14px 0}}.flow>div{{border:1px solid #a7b6d1;background:white;border-radius:8px;padding:12px;flex:1;min-width:150px}}.flow .arrow{{border:0;background:none;min-width:0;flex:0;align-self:center;font-size:1.4rem}}a{{color:#1f55ad}}.scroll{{overflow-x:auto}}.muted{{color:#53627b}}@media(max-width:700px){{main{{padding:18px 12px}}h1{{font-size:1.7rem}}.flow .arrow{{display:none}}td,th{{padding:8px;font-size:.85rem}}}}
</style><main>
<div class="hero"><p class="muted">Fresh live run · 3 November 2026 · Azure/gpt-4.1 · adapter commit 95c177f6</p>
<h1>Cortex + Evolve: revenue reconciliation rerun</h1><p class="lead">{verdict} Baseline <strong>{totals["baseline"]["passes"]}/6</strong>; Evolve <strong>{totals["evolve"]["passes"]}/6</strong>.</p>
<p>Start with the full comparison below, then inspect the learned guidelines and paired trajectories. All 12 outcomes are retained and were independently regraded from the saved answers and report files.</p>
<div class="stats"><div class="stat"><b>{totals["baseline"]["passes"]}/6 → {totals["evolve"]["passes"]}/6</b>Exact task passes</div><div class="stat"><b>{totals["baseline"]["turns"]} → {totals["evolve"]["turns"]}</b>Observed agent turns</div><div class="stat"><b>{totals["baseline"]["rejected"]} → {totals["evolve"]["rejected"]}</b>Rejected service calls</div></div></div>
<h2>Complete six-case comparison</h2><p>Each cell pair uses baseline / Evolve order. A pass requires exact per-currency integer totals, exact sorted event IDs, and the independently correct saved <code>report.json</code>. Timeouts count as failures.</p>
<div class="scroll"><table><thead><tr><th>Customer</th><th>Baseline</th><th>Evolve</th><th>Turns</th><th>Rejected service calls</th><th>Seconds</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p>Paired wins: {esc(", ".join(wins) or "none")}. Paired regressions: {esc(", ".join(regressions) or "none")}. Timeouts: {totals["baseline"]["timeouts"]} baseline; {totals["evolve"]["timeouts"]} Evolve. Rejected service calls count CLI/API errors logged by the billing service, rather than all Bash errors.</p>
<p>This is one paired trial per synthetic fixture using the same six fixtures as the previous experiment. Fresh sessions and storage prevent carryover. It is a bounded demonstration, not a statistical reliability estimate. The new guidelines and model variability mean this run does not isolate the causal effect of the trajectory adapter fixes. No billing-savings claim is made.</p>
<h2>Example: Orchid Analytics</h2><p>The baseline returned USD 255,604 and EUR 4,678 minor units. The correct values are USD 12,828 and EUR 4,472. Its date-prefix filtering retained stale revisions and included pending, sandbox, voided, and subsequently moved events; it added refund magnitudes.</p>
<p>The guided run used Python to keep the highest revision per event before filtering for production, settled status, and UTC month boundaries. It subtracted refunds within each currency and saved the exact report. Task completion changed from fail to pass; turns fell from 33 to 16; elapsed time fell from 128.05 to 51.83 seconds. Both runs had two rejected service calls.</p>
<p>This illustration was selected after observing the evaluation outcomes. The full comparison above retains every case, and no evaluation result influenced the frozen guidelines. <a href="example-orchid.json">Complete paired evidence</a>.</p>
<h2>Task and accounting rules</h2><p>The agent reconciles a specified month for a customer's production account using a local billing CLI, saves a report, and returns matching JSON. The raw export contains 15 ledger rows, including sandbox accounts, corrected events followed by stale replays, voided events, and events moved to another month. Refund amounts are positive magnitudes, and USD and EUR must stay separate.</p>
<ol><li>Discover the supported commands and resolve the production account.</li><li>Read the accounting contract and download the raw ledger.</li><li>Keep the greatest numeric revision per event across the entire export before filtering.</li><li>Filter production account, settled status, and the UTC half-open monthly interval. Parse timezone offsets.</li><li>Add captures and subtract refunds within each currency; sort the included event IDs.</li><li>Save exact integer totals and event IDs in <code>report.json</code>, then return that same JSON.</li></ol>
<h2>Key pieces and data flow</h2><p>Inference path for both arms:</p><div class="flow"><div>Claude Code agent<br>Task + Bash tools</div><span class="arrow">→</span><div>Cortex proxy<br>Record requests, results<br>Inject in Evolve arm</div><span class="arrow">→</span><div>Gateway bridge<br>Preserve all system text</div><span class="arrow">→</span><div>Model gateway<br>Azure/gpt-4.1</div></div>
<p>Training and reuse:</p><div class="flow"><div>Training session<br>Cortex evidence</div><span class="arrow">→</span><div>Trajectory adapter<br>Task, tools, results<br>Rules + metadata</div><span class="arrow">→</span><div>Evolve reflection<br>Standard + fast consistency</div><span class="arrow">→</span><div>Candidate review<br>Frozen serving namespace</div><span class="arrow">→</span><div>Cortex retrieval<br>Future-task guidelines</div></div>
<p>Automatic learning is disabled. Only Northwind Studio training evidence generated guidelines. Evaluation arm order alternates, and each invocation has the same 240-second timeout and $1 CLI budget cap. The bridge is identical in both arms because the configured gateway otherwise drops later Anthropic system text blocks.</p>
<h2>Training and generated guidelines</h2><p>Training succeeded in 15 turns with two rejected service calls. Standard reflection used an explicit objective to avoid rejected calls, while preserving task correctness as a separate criterion. Fast consistency analyzed the successful workflow. Four candidates were generated; candidates 1, 2, and 4 were promoted unchanged before evaluation. Generic coding-error recovery advice was excluded.</p>
{"".join(guideline_html)}
<p><a href="candidates.json">All candidates and provenance</a> · <a href="review.json">Pre-evaluation selection</a> · <a href="comparison-plan.json">Frozen plan</a></p>
<details><summary>Full recorded training trajectory</summary>{transcript("train-ledger")}</details>
<h2>Evidence reaching Evolve</h2><p>Each analysis received one captured batch with 14 tool calls and 14 matched results. Actual saved learning requests contain the original task, Bash tool contract, accounting contract, rejected commands, the Python error, and execution metadata. These are model-facing prompt captures, not only conversation renderings.</p>
<p>Standard reflection receives the explicit evaluator outcome. Fast consistency does not include that outcome in its generation prompt; it judges the recorded trajectory itself. This remains a limitation. Prompts remain bounded excerpts; the complete captured text is preserved in the learning input JSON files. This live training session did not exercise cross-batch or subagent paths; the harness batch fix is covered by unit regression tests.</p>
<details><summary>Recording audit</summary><pre>{pretty(audit)}</pre></details>
<p><a href="learning-train-ledger-standard-fewer-errors.prompts.jsonl">Actual standard learning prompt</a> · <a href="learning-train-ledger-consistency-correctness.prompts.jsonl">Actual fast-consistency prompt</a> · <a href="run-manifest.json">Source manifest</a> · <a href="harness.patch">Harness corrections used in this rerun</a></p>
<h2>All before-and-after trajectories</h2><p>Expand a customer, then expand either recorded conversation. Commands, observed tool results, and final answers are shown from the saved CLI stream. Each arm has a unique fresh session.</p>{"".join(case_details)}
<h2>Verify without model calls</h2><pre>env UV_CACHE_DIR=/private/tmp/rossocortex-uv-cache uv run --no-sync python -m experiments.cortex_workflow_learning.run --artifacts-dir experiments/cortex_workflow_learning/{esc(ROOT.name)} verify</pre>
<p><a href="verification.json">All 12 saved grades verified</a> · <a href="summary.json">Machine-readable full summary</a> · <a href="result-facts.json">Compact result facts</a></p>
<p class="muted">Generated {esc(facts["generated_at"])}. Prior report and results remain preserved in the experiment's REPORT.html and artifacts-postmerge directory.</p>
</main></html>"""
    (ROOT / "REPORT.html").write_text("\n".join(line.rstrip() for line in document.splitlines()) + "\n")
    print(json.dumps(facts, indent=2))


if __name__ == "__main__":
    main()
