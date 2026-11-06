"""Build a self-contained HTML report of one recorded experiment, offline, from its evidence.

    python demos/cortex-repo/report_html.py demos/cortex-repo/reports/sonnet45-20261106

Reads summary.json, state.json, candidates.json, promoted.json, review.json, the superseded
first learning pass and each session's trajectory; writes report.html beside them. No model
calls. Charts are inline SVG with hover tooltips; every charted value is also in a table.
"""

import html
import json
import sys
from pathlib import Path

ARMS = ("baseline", "guided")
ARM_NAMES = {"baseline": "Baseline", "guided": "With Evolve"}
HOOK_TRAPS = {
    "missing_changelog": "No CHANGES.md entry",
    "client_version": "No client VERSION bump",
    "commit_message": "Commit subject format",
}
TASK_NAMES = {"order-discount": "orders.discount_code", "product-weight": "products.weight_grams", "user-locale": "users.locale"}
HOOK_MARKERS = ("pre-commit: ", "commit-msg: ", "check failed: ")


def esc(value):
    return html.escape(str(value), quote=True)


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def messages(root, label):
    document = load(root / f"{label}-trajectory.json", {})
    return [m for t in document.get("trajectories") or [] for m in t.get("messages") or []]


def commit_story(rows):
    """Each git commit attempt and the hook messages that rejected it, in order."""
    calls = {}
    for message in rows:
        for call in message.get("tool_calls") or []:
            try:
                command = json.loads(call["function"]["arguments"]).get("command", "")
            except (ValueError, KeyError, AttributeError):
                command = ""
            calls[call.get("id")] = command
    story = []
    for message in rows:
        if message.get("role") != "tool":
            continue
        command = calls.get(message.get("tool_call_id"), "")
        if "git commit" not in command:
            continue
        output = str(message.get("content") or "")
        subject = command.split("-m", 1)[-1].strip().strip("\"'").splitlines()[0][:80] if "-m" in command else command[:80]
        reasons = [line.strip() for line in output.splitlines() if line.strip().startswith(HOOK_MARKERS)]
        story.append({"subject": subject, "accepted": not output.startswith("[error]"), "reasons": reasons})
    return story


def read_hooks_first(rows):
    """Did the agent open the hook scripts before its first commit attempt?"""
    for message in rows:
        for call in message.get("tool_calls") or []:
            arguments = call["function"].get("arguments", "")
            if "git commit" in arguments:
                return False
            if ".githooks" in arguments:
                return True
    return False


def bar_chart(title, subtitle, rows, unit="", max_value=None, fmt=str):
    """Horizontal grouped bars: one group per row, one bar per arm. rows: [(label, {arm: value})]."""
    max_value = max_value or max(v for _, values in rows for v in values.values()) or 1
    label_w, plot_w, bar_h, gap, group_gap = 170, 230, 16, 4, 16
    height = len(rows) * (2 * bar_h + gap + group_gap) + 10
    marks = []
    y = 6
    for label, values in rows:
        marks.append(f'<text x="{label_w - 12}" y="{y + bar_h + 2}" class="axis-label" text-anchor="end">{esc(label)}</text>')
        for index, arm in enumerate(ARMS):
            value = values[arm]
            width = max(2, plot_w * value / max_value)
            top = y + index * (bar_h + gap)
            tip = f"{ARM_NAMES[arm]} · {label}: {fmt(value)}{unit}"
            marks.append(
                f'<g class="mark" tabindex="0" data-tip="{esc(tip)}">'
                f'<rect x="{label_w}" y="{top - 3}" width="{plot_w + 60}" height="{bar_h + 6}" fill="transparent"/>'
                f'<path d="M{label_w},{top} h{width - 4} a4,4 0 0 1 4,4 v{bar_h - 8} a4,4 0 0 1 -4,4 h-{width - 4} z" class="bar {arm}"/>'
                f'<text x="{label_w + width + 8}" y="{top + bar_h - 5}" class="value">{esc(fmt(value))}{esc(unit)}</text></g>'
            )
        y += 2 * bar_h + gap + group_gap
    axis = f'<line x1="{label_w}" x2="{label_w}" y1="0" y2="{height - 6}" class="baseline"/>'
    legend = "".join(f'<span class="key"><span class="swatch {arm}"></span>{ARM_NAMES[arm]}</span>' for arm in ARMS)
    return (
        f'<figure class="chart"><figcaption><strong>{esc(title)}</strong><span>{esc(subtitle)}</span></figcaption>'
        f'<div class="legend">{legend}</div>'
        f'<svg width="{label_w + plot_w + 70}" viewBox="0 0 {label_w + plot_w + 70} {height}" role="img" aria-label="{esc(title)}">{axis}{"".join(marks)}</svg></figure>'
    )


def pair_strip(pairs):
    """Rejected commits per pair, baseline vs guided, as dot pairs on one shared scale."""
    left, step, top, row_h = 250, 90, 24, 38
    width = left + 3 * step + 40
    height = top + len(pairs) * row_h + 16
    parts = [f'<text x="{left + i * step}" y="14" class="axis-label" text-anchor="middle">{i}</text>' for i in range(3)]
    parts += [f'<line x1="{left + i * step}" x2="{left + i * step}" y1="20" y2="{height - 8}" class="grid"/>' for i in range(3)]
    for index, pair in enumerate(pairs):
        y = top + index * row_h + 12
        label = f"Pair {pair['pair']} · {TASK_NAMES.get(pair['task'], pair['task'])}"
        parts.append(f'<text x="{left - 24}" y="{y + 4}" class="axis-label" text-anchor="end">{esc(label)}</text>')
        b, g = pair["baseline"], pair["guided"]
        if b != g:
            parts.append(f'<line x1="{left + b * step}" x2="{left + g * step}" y1="{y - 7}" y2="{y + 7}" class="connector"/>')
        for arm, value, nudge in (("baseline", b, -7), ("guided", g, 7)):
            tip = f"{ARM_NAMES[arm]} · pair {pair['pair']}: {value} rejected commit{'s' if value != 1 else ''}"
            parts.append(
                f'<g class="mark" tabindex="0" data-tip="{esc(tip)}"><circle cx="{left + value * step}" cy="{y + nudge}" r="14" fill="transparent"/>'
                f'<circle cx="{left + value * step}" cy="{y + nudge}" r="6" class="dot {arm}"/></g>'
            )
    legend = "".join(f'<span class="key"><span class="dotkey {arm}"></span>{ARM_NAMES[arm]}</span>' for arm in ARMS)
    return (
        '<figure class="chart"><figcaption><strong>Rejected commits in each pair</strong>'
        "<span>Every baseline was rejected twice. Guided runs ranged from zero to two.</span></figcaption>"
        f'<div class="legend">{legend}</div><svg width="{width}" viewBox="0 0 {width} {height}" role="img" aria-label="Rejected commits per pair">{"".join(parts)}</svg></figure>'
    )


def table(headers, rows, numeric=()):
    head = "".join(f'<th class="{"num" if i in numeric else ""}">{esc(h)}</th>' for i, h in enumerate(headers))
    body = "".join(
        "<tr>" + "".join(f'<td class="{"num" if i in numeric else ""}">{cell}</td>' for i, cell in enumerate(row)) + "</tr>" for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def story_html(story):
    items = []
    for attempt in story:
        state = "accepted" if attempt["accepted"] else "rejected"
        reasons = "".join(f"<li>{esc(r)}</li>" for r in attempt["reasons"] if not r.startswith("check passed"))
        items.append(
            f'<li class="{state}"><span class="badge {state}">{"✓ accepted" if attempt["accepted"] else "✕ rejected"}</span> '
            f"<code>{esc(attempt['subject'])}</code>{f'<ul>{reasons}</ul>' if reasons and not attempt['accepted'] else ''}</li>"
        )
    return f'<ol class="story">{"".join(items)}</ol>'


def build(root):
    summary = load(root / "summary.json")
    state = load(root / "state.json")
    promoted = load(root / "promoted.json", [])
    candidates = load(root / "candidates.json", [])
    review = load(root / "review.json", {})
    first_pass = load(root / "superseded" / "candidates.json", [])
    arms = summary["arms"]
    runs = summary["runs"]
    by_label = {run["label"]: run for run in runs}

    stories = {run["label"]: commit_story(messages(root, run["label"])) for run in runs}
    hooks_first = {run["label"]: read_hooks_first(messages(root, run["label"])) for run in runs}
    pairs = []
    for number in range(1, len(runs) // 2 + 1):
        b, g = by_label[f"baseline-{number}"], by_label[f"guided-{number}"]
        pairs.append({"pair": number, "task": b["task"], "baseline": b["commit_rejections"], "guided": g["commit_rejections"]})

    tiles = [
        (
            "Commits accepted first time",
            f"{arms['baseline']['runs_committing_first_try']}/6",
            f"{arms['guided']['runs_committing_first_try']}/6",
        ),
        ("Rejected commits", f"{arms['baseline']['total_commit_rejections']}", f"{arms['guided']['total_commit_rejections']}"),
        ("Correct and committed", f"{arms['baseline']['passed']}/6", f"{arms['guided']['passed']}/6"),
        ("Cost per run", f"${arms['baseline']['mean_cost_usd']:.2f}", f"${arms['guided']['mean_cost_usd']:.2f}"),
    ]
    tile_html = "".join(
        f'<div class="tile"><div class="tile-label">{esc(label)}</div><div class="tile-values">'
        f'<div><span class="tile-arm"><span class="swatch baseline"></span>Baseline</span><span class="tile-value">{esc(b)}</span></div>'
        f'<div><span class="tile-arm"><span class="swatch guided"></span>With Evolve</span><span class="tile-value">{esc(g)}</span></div></div></div>'
        for label, b, g in tiles
    )

    reasons_chart = bar_chart(
        "Runs rejected for each hook rule",
        "Out of six runs per arm. Guided runs missed each rule less often, but still missed them.",
        [(name, {arm: arms[arm]["runs_hitting"][key] for arm in ARMS}) for key, name in HOOK_TRAPS.items()],
        unit="/6",
        max_value=6,
    )
    cost_chart = bar_chart(
        "Effort per run",
        "Means over six runs. Saved rejections were spent reading the hook scripts instead.",
        [
            ("Tool calls", {arm: arms[arm]["mean_tool_calls"] for arm in ARMS}),
            ("Failed tool calls", {arm: arms[arm]["mean_tool_errors"] for arm in ARMS}),
        ],
        fmt=lambda v: f"{v:.1f}",
    )

    run_rows = []
    for number in range(1, len(pairs) + 1):
        for arm in ARMS if number % 2 else ARMS[::-1]:
            run = by_label[f"{arm}-{number}"]
            rules = ", ".join(HOOK_TRAPS[k] for k in HOOK_TRAPS if run["trap_hits"][k]) or "—"
            run_rows.append(
                [
                    esc(run["label"]),
                    esc(TASK_NAMES.get(run["task"], run["task"])),
                    "pass" if run["passed"] else "<b>FAIL</b>",
                    str(run["commit_rejections"]),
                    esc(rules),
                    "yes" if hooks_first[run["label"]] else "no",
                    str(run["tool_calls"]),
                    f"${run['cost_usd']:.3f}",
                    esc(",".join(map(str, run["injected_ids"])) or "—"),
                    f'<a href="{esc(run["label"])}-trajectory.json">json</a>',
                ]
            )
    runs_table = table(
        [
            "Run",
            "Field added",
            "Grader",
            "Rejected commits",
            "Rules missed",
            "Read hooks first",
            "Tool calls",
            "Cost",
            "Injected",
            "Trajectory",
        ],
        run_rows,
        numeric=(3, 6, 7),
    )

    promoted_ids = {str(p["candidate_id"]) for p in promoted}
    learned = "".join(
        f'<li class="{"kept" if str(c["id"]) in promoted_ids else "dropped"}"><span class="badge {"accepted" if str(c["id"]) in promoted_ids else "neutral"}">'
        f"{'promoted' if str(c['id']) in promoted_ids else 'not promoted'}</span> {esc(c['content'])}"
        f"{'<div class=note>' + esc(review.get('rejected', {}).get(str(c['id']), '')) + '</div>' if str(c['id']) not in promoted_ids else ''}</li>"
        for c in candidates
    )
    first = "".join(f"<li>{esc(c['content'])}</li>" for c in first_pass)
    training = summary["training"]
    training_story = story_html(commit_story(messages(root, "training")))
    compare = (
        '<div class="compare">'
        f'<div><h4><span class="swatch baseline"></span>baseline-3 · users.locale</h4>{story_html(stories["baseline-3"])}</div>'
        f'<div><h4><span class="swatch guided"></span>guided-3 · users.locale</h4>{story_html(stories["guided-3"])}'
        '<p class="note">Read <code>.githooks/pre-commit</code> and <code>commit-msg</code> before the first attempt.</p></div></div>'
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evolve learns a repository's conventions — experiment report</title>
<style>
.viz-root{{color-scheme:light;--surface-1:#fcfcfb;--surface-2:#f4f3f0;--text-primary:#0b0b0b;--text-secondary:#52514e;--muted:#898781;--grid:#e1e0d9;
--series-1:#2a78d6;--series-2:#eb6834;--good:#0a7a0a;--bad:#b42f2f}}
@media (prefers-color-scheme:dark){{:root:where(:not([data-theme="light"])) .viz-root{{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;
--text-primary:#fff;--text-secondary:#c3c2b7;--muted:#898781;--grid:#2c2c2a;--series-1:#3987e5;--series-2:#d95926;--good:#3fbf3f;--bad:#e66767}}}}
:root[data-theme="dark"] .viz-root{{color-scheme:dark;--surface-1:#1a1a19;--surface-2:#242422;--text-primary:#fff;--text-secondary:#c3c2b7;--muted:#898781;
--grid:#2c2c2a;--series-1:#3987e5;--series-2:#d95926;--good:#3fbf3f;--bad:#e66767}}
body{{margin:0}}
.viz-root{{background:var(--surface-1);color:var(--text-primary);font:16px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;min-height:100vh}}
main{{max-width:1040px;margin:0 auto;padding:40px 24px 80px}}
h1{{font-size:30px;line-height:1.2;margin:0 0 8px}} h2{{font-size:21px;margin:48px 0 8px}} h3{{font-size:17px;margin:24px 0 6px}} h4{{margin:0 0 8px;font-size:15px}}
p,li{{color:var(--text-secondary)}} strong,b{{color:var(--text-primary)}} a{{color:var(--series-1)}}
.lede{{font-size:18px;max-width:780px}}
.answer{{background:var(--surface-2);border-radius:10px;padding:18px 22px;margin:24px 0}}
.answer p{{margin:6px 0;color:var(--text-primary)}}
.tiles{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:24px 0}}
.tile{{background:var(--surface-2);border-radius:10px;padding:14px 16px}}
.tile-label{{font-size:14px;color:var(--text-secondary);margin-bottom:8px}}
.tile-values div{{display:flex;justify-content:space-between;align-items:baseline}}
.tile-arm{{font-size:13px;color:var(--text-secondary)}} .tile-value{{font-size:26px;font-weight:600}}
.swatch{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:baseline}}
.swatch.baseline,.bar.baseline,.dot.baseline{{background:var(--series-1);fill:var(--series-1)}}
.swatch.guided,.bar.guided,.dot.guided{{background:var(--series-2);fill:var(--series-2)}}
.dotkey{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}} .dotkey.baseline{{background:var(--series-1)}} .dotkey.guided{{background:var(--series-2)}}
.dot{{stroke:var(--surface-1);stroke-width:2}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(470px,1fr));gap:20px}}
.chart{{margin:16px 0;background:var(--surface-1);border:1px solid var(--grid);border-radius:10px;padding:16px}}
.chart figcaption{{display:flex;flex-direction:column;gap:2px;margin-bottom:6px}} .chart figcaption span{{font-size:14px;color:var(--text-secondary)}}
.legend{{display:flex;gap:16px;font-size:13px;color:var(--text-secondary);margin:4px 0 8px}}
svg{{max-width:100%;height:auto;display:block;overflow:visible}}
.axis-label{{font-size:13px;fill:var(--text-secondary)}} .value{{font-size:13px;fill:var(--text-primary);font-variant-numeric:tabular-nums}}
.baseline{{stroke:var(--muted);stroke-width:1}} .grid{{stroke:var(--grid);stroke-width:1}} .connector{{stroke:var(--muted);stroke-width:2;stroke-linecap:round}}
.mark{{cursor:default;outline:none}} .mark:hover .bar,.mark:focus .bar,.mark:hover .dot,.mark:focus .dot{{opacity:.8}}
#tip{{position:fixed;pointer-events:none;background:var(--text-primary);color:var(--surface-1);font-size:13px;padding:6px 9px;border-radius:6px;opacity:0;transition:opacity .08s;z-index:5}}
table{{border-collapse:collapse;width:100%;font-size:14px;margin:8px 0 16px}}
th,td{{text-align:left;padding:7px 8px;border-bottom:1px solid var(--grid);vertical-align:top}} th{{color:var(--text-secondary);font-weight:600}}
td{{color:var(--text-primary)}} td:first-child,td:nth-child(2){{white-space:nowrap}} .num{{text-align:right;font-variant-numeric:tabular-nums}}
code{{background:var(--surface-2);padding:1px 5px;border-radius:4px;font-size:13px;color:var(--text-primary)}}
.flow{{display:grid;grid-template-columns:repeat(5,1fr);gap:8px;margin:16px 0}}
.flow div{{background:var(--surface-2);border-radius:8px;padding:10px 12px;font-size:14px;color:var(--text-secondary)}} .flow b{{display:block;margin-bottom:2px}}
.story{{padding-left:20px;margin:6px 0}} .story li{{margin:6px 0}} .story ul{{margin:4px 0 0;padding-left:18px;font-size:13px}}
.badge{{display:inline-block;font-size:12px;font-weight:600;padding:1px 7px;border-radius:9px;margin-right:4px}}
.badge.accepted{{color:var(--good);border:1px solid var(--good)}} .badge.rejected{{color:var(--bad);border:1px solid var(--bad)}} .badge.neutral{{color:var(--muted);border:1px solid var(--muted)}}
.compare{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .compare>div{{background:var(--surface-2);border-radius:10px;padding:14px 16px}}
.learned{{list-style:none;padding:0}} .learned li{{margin:10px 0}} .learned li.dropped{{opacity:.75}}
.note{{font-size:13px;color:var(--muted);margin-top:4px}}
.theme{{position:absolute;top:16px;right:20px;font-size:13px}}
@media (max-width:720px){{.flow,.compare{{grid-template-columns:1fr}} .charts{{grid-template-columns:1fr}}}}
@media print{{.theme{{display:none}}}}
</style></head>
<body><div class="viz-root"><button class="theme" type="button" onclick="toggleTheme()">Light / dark</button><main>
<h1>Evolve learns a repository's conventions</h1>
<p class="lede">Can Evolve learn a repository's unwritten rules from one ordinary Claude Code session, with nobody grading or correcting it, so that the next sessions stop running into them?</p>
<div class="answer"><p><b>Partly.</b> Every baseline run had its commit rejected twice. With Evolve's learned guidelines injected, 2 of 6 runs committed first time and rejections fell from 12 to 7.
Correctness tied (6/6 each) and cost was flat. Why it is not larger: the guidelines told the agent how to <i>react</i> to a rejection, not what the rules were.</p></div>
<div class="tiles">{tile_html}</div>

<h2>The setup</h2>
<p>A small schema-driven Python library, <code>shopkit</code>, whose git hooks reject a commit unless it has regenerated code and a migration, an entry in <code>CHANGES.md</code>,
a bump of <code>clients/python/VERSION</code>, and a lower-case Conventional Commit subject. The task prompt mentions none of this.</p>
<div class="flow"><div><b>1 · Train</b>Add <code>users.nickname</code>. Cortex records the session.</div><div><b>2 · Learn</b>Evolve reads the recorded session. No feedback, no grade.</div>
<div><b>3 · Review</b>A person promotes 4 of 5 candidates.</div><div><b>4 · Evaluate</b>6 baseline/guided pairs on 3 different fields, interleaved.</div>
<div><b>5 · Grade</b>An offline grader checks each committed repository.</div></div>
<p>Everything ran in Docker: Claude Code {esc(state.get("claude_version", "2.1.287"))}, Cortex (<code>placement: user</code>), Evolve, the repository and its toolchain.
Task and learner model <code>{esc(state["model"])}</code>. Every session starts from a fresh clone.</p>

<h2>What happened in training</h2>
<p>The training session succeeded after {training["commit_rejections"]} rejected commits ({training["tool_calls"]} tool calls, ${training["cost_usd"]:.2f}). That is the only evidence Evolve had:</p>
{training_story}

<h2>What Evolve learned</h2>
<ul class="learned">{learned}</ul>
<details><summary>First learning pass, discarded</summary><p>For a Cortex session Evolve renders at most 50 steps: the first 40 and the last 10. Training ran 61 steps,
so steps 41–51 were dropped. That removed the first rejection (step 44: the missing CHANGES.md entry and client VERSION bump) and kept only the second (step 54: the
commit subject format). The first pass mentions commit-message formats but nothing about the changelog or version rules. The demo now renders up to 150 steps.</p><ul>{first}</ul></details>

<h2>Results</h2>
{pair_strip(pairs)}
<div class="charts">{reasons_chart}{cost_chart}</div>
<h3>Baseline vs guided on the same task</h3>
{compare}

<h2>Why the effect is modest</h2>
<ul>
<li><b>The lesson that would help most was generalised away.</b> Evolve learned "when commit hooks reject a commit, read the error for all required companion changes".
That is advice for <i>after</i> a rejection. It never stated the rules themselves, so four of six guided runs still had to be rejected once to discover them.
Evolve's prompt asks for general guidelines without task-specific values, which strips out exactly the repository-specific facts that would prevent the error.</li>
<li><b>The hooks force correctness.</b> A run cannot finish without fixing its commit, so both arms always pass. The benefit can only show up as avoided rejections.</li>
<li><b>Six pairs is a small sample.</b> The direction is consistent (no guided run did worse than its baseline), but the size is not established.</li>
</ul>

<h2>Next</h2>
<ul><li>Let Evolve keep concrete facts scoped to a repository: "in this repository, a schema change needs a CHANGES.md entry, a clients/python/VERSION bump, and a lower-case Conventional Commit subject". Rerun the six pairs.</li>
<li>Raise Evolve's default 50-step window for Cortex coding sessions, or always keep failed steps and the recovery after them, so lessons in the middle of a long session are not dropped.</li></ul>

<h2>Every run</h2>
{runs_table}
<details><summary>Data behind the charts</summary>
{table(["Measure", "Baseline", "With Evolve"], [[esc(n), esc(arms["baseline"]["runs_hitting"][k]) + "/6", esc(arms["guided"]["runs_hitting"][k]) + "/6"] for k, n in HOOK_TRAPS.items()] + [["Mean tool calls", f"{arms['baseline']['mean_tool_calls']:.1f}", f"{arms['guided']['mean_tool_calls']:.1f}"], ["Mean failed tool calls", f"{arms['baseline']['mean_tool_errors']:.1f}", f"{arms['guided']['mean_tool_errors']:.1f}"], ["Total cost", f"${arms['baseline']['total_cost_usd']:.2f}", f"${arms['guided']['total_cost_usd']:.2f}"]], numeric=(1, 2))}
</details>

<h2>Method and limits</h2>
<ul>
<li>Baseline and guided alternate, and so does the arm that runs first. Each pair uses the same field; pairs rotate through three fields.</li>
<li>Guidelines were frozen before any evaluation session and injected on the first user message of every guided session (ids 1–4, verified on each session's first request). No baseline received them.</li>
<li>A rejected commit is a failed <code>git commit</code> whose output contains a hook message. Reading the hook scripts does not count.</li>
<li>The grader copies each repository into a network-disabled container and checks: clean tree, a new commit, the pre-merge check, migrations apply and add the column, a value stored and loaded back, the test suite, the changelog entry, the client version bump, and Conventional Commit subjects.</li>
<li>One model, one repository, six pairs. Costs are Claude Code's reported totals; they exclude Evolve's learning call.</li>
</ul>
<p class="note">Generated by <code>demos/cortex-repo/report_html.py</code> from the evidence in this directory: <a href="summary.json">summary.json</a> · <a href="state.json">state.json</a> · <a href="candidates.json">candidates.json</a> · <a href="promoted.json">promoted.json</a> · <a href="results.md">results.md</a></p>
</main><div id="tip" role="tooltip"></div></div>
<script>
const tip = document.getElementById("tip");
function show(event) {{
  const mark = event.target.closest(".mark");
  if (!mark) return;
  tip.textContent = mark.dataset.tip;
  const box = mark.getBoundingClientRect();
  const x = event.clientX ?? box.right, y = event.clientY ?? box.top;
  tip.style.left = Math.min(x + 12, window.innerWidth - tip.offsetWidth - 8) + "px";
  tip.style.top = (y - 34) + "px";
  tip.style.opacity = 1;
}}
function hide() {{ tip.style.opacity = 0; }}
document.querySelectorAll(".mark").forEach(m => {{
  m.addEventListener("pointermove", show); m.addEventListener("pointerleave", hide);
  m.addEventListener("focus", show); m.addEventListener("blur", hide);
}});
function toggleTheme() {{
  const root = document.documentElement;
  const dark = root.dataset.theme ? root.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "light" : "dark";
}}
</script></body></html>
"""


if __name__ == "__main__":
    root = Path(sys.argv[1])
    (root / "report.html").write_text(build(root))
    print(root / "report.html")
