"""Grade every session and compare baseline with guided, offline.

Runs in a network-disabled container with the results mounted. Writes summary.json and
summary.html. Trap counts come from the recorded tool calls and results, not from the
agent's own account.
"""

import argparse
import html
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import grade  # noqa: E402

# Each trap and the failing command output that shows the agent hit it.
TRAPS = {
    "plain_pytest": "SHOPKIT_DB is not set",
    "stale_generated": "is stale: run `make generate`",
    "missing_migration": "migrations do not create",
    "missing_changelog": "without an entry in CHANGES.md",
    "partial_staging": "part of the change is not staged",
    "client_version": "bump clients/python/VERSION",
    "commit_message": "subject must be a Conventional Commit",
}


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def messages(results, label):
    document = load(results / f"{label}-trajectory.json", {})
    return [m for t in document.get("trajectories") or [] for m in t.get("messages") or []]


def failed_outputs(rows):
    """Output of commands that failed. Reading a file that contains the same text is not a hit."""
    return [str(m.get("content") or "") for m in rows if m.get("role") == "tool" and str(m.get("content") or "").startswith("[error]")]


def trap_hits(rows):
    outputs = failed_outputs(rows)
    return {name: sum(marker in output for output in outputs) for name, marker in TRAPS.items()}


def commit_rejections(rows):
    hooks = ("pre-commit: ", "commit-msg: ", "check failed: ")
    return sum(any(hook in output for hook in hooks) for output in failed_outputs(rows))


def metrics(results, state, label):
    session = state["sessions"][label]
    rows = messages(results, label)
    cli = load(results / f"{label}-cli.json", {}) or {}
    verdict = grade.grade(results / "tasks" / label, session["task"])
    hits = trap_hits(rows)
    return {
        "label": label,
        "arm": session["arm"],
        "task": session["task"].get("id", "training"),
        "passed": verdict["passed"],
        "checks": verdict["checks"],
        "tool_calls": sum(len(m.get("tool_calls") or []) for m in rows),
        "tool_errors": sum(1 for m in rows if m.get("role") == "tool" and str(m.get("content", "")).startswith("[error]")),
        "trap_hits": hits,
        "traps_hit": sum(1 for count in hits.values() if count),
        "commit_rejections": commit_rejections(rows),
        "cost_usd": cli.get("total_cost_usd"),
        "turns": cli.get("num_turns"),
        "exit_code": session.get("exit_code"),
        "injected_ids": (session.get("first_request_evolve") or {}).get("ids")
        if (session.get("first_request_evolve") or {}).get("injected")
        else [],
    }


def aggregate(rows):
    def stat(key, fn):
        values = [row[key] for row in rows if row[key] is not None]
        return round(fn(values), 3) if values else None

    return {
        "runs": len(rows),
        "passed": sum(row["passed"] for row in rows),
        "mean_traps_hit": stat("traps_hit", statistics.mean),
        "total_commit_rejections": stat("commit_rejections", sum),
        "runs_committing_first_try": sum(1 for row in rows if row["commit_rejections"] == 0),
        "median_tool_calls": stat("tool_calls", statistics.median),
        "mean_tool_calls": stat("tool_calls", statistics.mean),
        "mean_tool_errors": stat("tool_errors", statistics.mean),
        "mean_cost_usd": stat("cost_usd", statistics.mean),
        "total_cost_usd": stat("cost_usd", sum),
        "runs_hitting": {name: sum(1 for row in rows if row["trap_hits"][name]) for name in TRAPS},
    }


def esc(value):
    return html.escape(str(value))


def render(results, summary, state, candidates, promoted):
    arms = summary["arms"]
    head = "<tr><th></th><th>Baseline</th><th>With Evolve</th></tr>"
    lines = [
        ("Correct and committed", lambda a: f"{a['passed']}/{a['runs']}"),
        ("Commits accepted on the first try", lambda a: f"{a['runs_committing_first_try']}/{a['runs']}"),
        ("Rejected commits (total)", lambda a: a["total_commit_rejections"]),
        ("Traps hit per run (mean)", lambda a: a["mean_traps_hit"]),
        ("Tool calls (median)", lambda a: a["median_tool_calls"]),
        ("Tool errors (mean)", lambda a: a["mean_tool_errors"]),
        ("Cost per run, USD (mean)", lambda a: a["mean_cost_usd"]),
    ]
    rows = "".join(
        f"<tr><td>{esc(name)}</td><td>{esc(fn(arms['baseline']))}</td><td>{esc(fn(arms['guided']))}</td></tr>" for name, fn in lines
    )
    traps = "".join(
        f"<tr><td><code>{esc(name)}</code></td><td>{arms['baseline']['runs_hitting'][name]}/{arms['baseline']['runs']}</td>"
        f"<td>{arms['guided']['runs_hitting'][name]}/{arms['guided']['runs']}</td></tr>"
        for name in TRAPS
    )
    runs = "".join(
        f"<tr><td>{esc(r['label'])}</td><td>{esc(r['task'])}</td><td>{'pass' if r['passed'] else 'FAIL'}</td><td>{r['traps_hit']}</td>"
        f"<td>{r['tool_calls']}</td><td>{esc(r['cost_usd'])}</td><td>{esc(','.join(map(str, r['injected_ids'] or [])))}</td>"
        f'<td><a href="{esc(r["label"])}-trajectory.json">trajectory</a></td></tr>'
        for r in summary["runs"]
    )
    learned = "".join(f"<li>{esc(row['content'])}</li>" for row in promoted)
    rejected = "".join(
        f"<li>{esc(row['content'])}</li>" for row in candidates if str(row["id"]) not in {str(p["candidate_id"]) for p in promoted}
    )
    training = summary["training"]
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Evolve learns a repository's conventions</title><style>body{{max-width:980px;margin:32px auto;padding:0 20px;font:16px/1.55 system-ui;color:#172033}}
table{{border-collapse:collapse;width:100%;margin:12px 0 24px}}td,th{{text-align:left;padding:8px;border-bottom:1px solid #ccd}}code{{background:#eef2f6;padding:1px 4px}}
li{{margin:6px 0}}.note{{color:#555}}</style></head><body>
<h1>Evolve learns a repository's conventions</h1>
<p>Fresh Claude Code sessions on the same small repository keep tripping over conventions they cannot guess. Cortex recorded one training
session; Evolve learned from its errors with <b>no grading and no feedback</b>; Cortex then injected the reviewed guidelines into new sessions
doing <b>different</b> changes.</p>
<table>{head}{rows}</table>
<h2>The conventions</h2><table><tr><th>Trap</th><th>Baseline runs hitting it</th><th>Guided runs hitting it</th></tr>{traps}</table>
<p class="note">plain_pytest: running pytest directly instead of <code>./scripts/test</code>. stale_generated: editing the schema without
<code>make generate</code>. missing_migration, missing_changelog, partial_staging, client_version, commit_message: commits rejected by
the repository's git hooks.</p>
<h2>What Evolve learned</h2><p>From the training session ({esc(training["label"])}: {training["traps_hit"]} traps hit, {training["tool_calls"]} tool calls,
{"passed" if training["passed"] else "did not pass"}). Promoted unchanged:</p><ul>{learned}</ul>
{f"<p>Not promoted:</p><ul>{rejected}</ul>" if rejected else ""}
<h2>Every run</h2><table><tr><th>Run</th><th>Task</th><th>Result</th><th>Traps</th><th>Calls</th><th>USD</th><th>Injected</th><th></th></tr>{runs}</table>
<p class="note">Model {esc(state["model"])}; learner {esc(state["learning_model"])}; placement {esc(state.get("placement") or "system")}.
Baseline and guided sessions alternate, with the arm that goes first alternating too; each starts from a fresh clone. Grading copies each
repository into a network-disabled container and checks the committed result: pre-merge check, migrations, a stored and loaded value, the
test suite, and a changelog entry.</p></body></html>"""


def report(results):
    state = load(results / "state.json")
    rows = [metrics(results, state, label) for label in state["sessions"] if state["sessions"][label].get("captured")]
    training = next(row for row in rows if row["arm"] == "training")
    evaluated = [row for row in rows if row["arm"] in ("baseline", "guided")]
    summary = {
        "model": state["model"],
        "placement": state.get("placement"),
        "training": training,
        "arms": {arm: aggregate([row for row in evaluated if row["arm"] == arm]) for arm in ("baseline", "guided")},
        "runs": evaluated,
    }
    (results / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    page = render(results, summary, state, load(results / "candidates.json", []), load(results / "promoted.json", []))
    (results / "summary.html").write_text(page)
    for arm in ("baseline", "guided"):
        print(arm, json.dumps({k: v for k, v in summary["arms"][arm].items()}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results", type=Path, default=Path("/results"))
    report(parser.parse_args().results)
