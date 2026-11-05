"""Build a single report after all three fresh pairs have completed."""

import html
import json
from pathlib import Path

import evaluate_trials


def esc(value):
    return html.escape(str(value))


evaluate_trials.main()
root = Path(__file__).parent
summary = json.loads((root / "summary.json").read_text())
assert summary["completed_pairs"] == 3
plan = json.loads((root / "plan.json").read_text())
baseline = summary["totals"]["baseline"]
guided = summary["totals"]["guided"]
rows = []
sessions = []
for pair in summary["pairs"]:
    number = pair["trial"]
    a, b = pair["metrics"]["baseline"], pair["metrics"]["guided"]
    state = json.loads((root / f"trial-{number}/state.json").read_text())
    rows.append(
        f'<tr><td><a href="trial-{number}/comparison.html">{number}</a></td>'
        f"<td>{a['tool_calls']}</td><td>{b['tool_calls']}</td>"
        f"<td>{esc(a['numeric_correct'])}</td><td>{esc(b['numeric_correct'])}</td>"
        f"<td>{esc(json.dumps(a['actual']))} / {esc(json.dumps(b['actual']))}</td>"
        f"<td>{esc(pair['reviewed_ids_injected'])}</td></tr>"
    )
    sessions.append(
        f"<li>Pair {number}: baseline {esc(state['sessions']['baseline']['id'])}; "
        f"guided {esc(state['sessions']['guided']['id'])}. "
        f'<a href="trial-{number}/baseline-trajectory.json">Before JSON</a> · '
        f'<a href="trial-{number}/guided-trajectory.json">After JSON</a></li>'
    )
guidelines = "".join(
    f"<article><p>Candidate {esc(row['candidate_id'])} → serving {esc(row['id'])}</p>"
    f"<blockquote>{esc(row['content'])}</blockquote></article>"
    for row in plan["selected_candidates"]
)
verdict = (
    f"Numeric exports passed in {baseline['correct_numeric_exports']}/3 baseline and "
    f"{guided['correct_numeric_exports']}/3 guided attempts. Total tool calls: "
    f"{baseline['tool_calls']} baseline, {guided['tool_calls']} guided."
)
improved_pairs = [
    p["trial"] for p in summary["pairs"] if p["metrics"]["guided"]["numeric_correct"] and not p["metrics"]["baseline"]["numeric_correct"]
]
if improved_pairs:
    interpretation = (
        f"<p>Numeric correctness improved in pair(s) {esc(improved_pairs)}. In pair 3, the guided session used "
        "<code>exiftool -j -n sample.jpg &gt; photo-result.json &amp;&amp; cat photo-result.json</code>, "
        "matching the numeric-export advice. It saved numeric 4.38 and 27 in three tool calls, while its baseline used four calls and saved a string.</p>"
        "<p>The first two pairs still failed numeric JSON correctness and each used one extra guided tool call. "
        "One of three pairs improved; there was no overall tool-call reduction. This provides a concrete observed win, "
        "with inconsistent transfer across repeats. It does not isolate causation from ordinary model variation.</p>"
    )
    next_task = '<p>An alternative invoice-extraction task was drafted while the trials ran. Since this batch contains a correctness improvement, it has not been run. <a href="next-task.md">Optional alternative task design</a>.</p>'
else:
    interpretation = (
        "<p>The photo experiment repeatedly encountered the same issue: Python serialization converted the rational focal value to a string. "
        "The reviewed advice focused on numeric GPS export and separate display/export procedures; it did not directly address Pillow's rational serialization.</p>"
    )
    next_task = (
        "<p>The proposed next task is to extract invoice PDF line items into a reconciled CSV. Mixed text/scanned pages, repeated headers and wrapped descriptions create consequential tool-selection and validation decisions. "
        "Evolve could learn a procedure from real extraction errors and transfer it to unseen invoices. This is a proposal; no invoice task has been run.</p>"
        '<p><a href="next-task.md">Full proposed task and evaluation design</a>.</p>'
    )
document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Three Sonnet photo trials</title>
<style>body{{max-width:1100px;margin:40px auto;padding:0 24px;font:16px/1.6 system-ui;color:#172033;background:#f8fafc;overflow-wrap:anywhere}}h1,h2{{line-height:1.2}}table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#eef2f6;padding:18px}}.verdict{{padding:20px;background:#e8f0ff;border-left:5px solid #0f62fe;font-size:20px}}article{{background:white;padding:18px;margin:12px 0}}blockquote{{margin:12px 0;border-left:3px solid #0f62fe;padding-left:16px}}a{{color:#0043ce}}.scroll{{overflow-x:auto}}</style>
<h1>Three fresh Sonnet 4.5 baseline/guided pairs</h1>
<p class="verdict">{esc(verdict)}</p>
<p>All six task sessions used <code>aws/claude-sonnet-4-5</code> in interactive Claude Code 2.1.287. The same original JPEG, tools and task prompt were supplied to both arms. The two reviewed Sonnet-generated guidelines were frozen; no task run was used for new learning, and no corrective coaching was supplied.</p>
<h2>Measured results</h2>
<div class="scroll"><table><thead><tr><th>Pair</th><th>Baseline calls</th><th>Guided calls</th><th>Baseline numeric pass</th><th>Guided numeric pass</th><th>Saved focal length: before / after</th><th>Reviewed IDs recorded as injected</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>
<p>Numeric correctness requires the native focal tag to contain numeric 4.38 and the equivalent tag numeric 27. Strings fail. The checker accepts the native tag keys inside a JSON wrapper; it imposes no unstated top-level shape requirement. Tool counts include all captured calls. Elapsed time includes approvals and an interruption and is excluded from efficiency comparisons.</p>
<h2>Before and after trajectories</h2>
<p>Open a pair number above for its architecture, exact output, generated/reviewed guidelines, attribution, and expandable before/training/after messages. The baseline and guided sessions below are new; their common GPS learning recording was reused unchanged.</p>
<ul>{"".join(sessions)}</ul>
<h2>Frozen guidelines</h2>{guidelines}
<p>Source GPS session: {esc(plan["source_training_session"])}. Every guided first main request must record these serving IDs, revisions and training sources. The final outbound payload was not independently inspected.</p>
<h2>Interpretation</h2>{interpretation}{next_task}
<p><a href="summary.json">Computed metrics</a> · <a href="plan.json">Frozen trial plan</a> · <a href="evaluate_trials.py">Evaluator</a></p>
<p>This is a bounded three-pair observation, not an estimate of Evolve's performance across tasks. All failures and trajectories are retained.</p>
</html>"""
(root / "summary.html").write_text(document)
print("Report:", root / "summary.html")
