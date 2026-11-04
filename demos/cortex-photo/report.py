"""Build a standalone report from this run's evidence, without model calls."""

import html
import json
import math
from pathlib import Path


def esc(value):
    return html.escape(str(value))


def metadata_check(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"actual": None, "equivalent": None, "numeric_correct": False, "error": "Missing or invalid JSON"}
    tags = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else data
    if not isinstance(tags, dict):
        return {"actual": None, "equivalent": None, "numeric_correct": False, "error": "Expected an object or a list containing one"}
    actual = tags.get("FocalLength")
    equivalent = tags.get("FocalLengthIn35mmFormat", tags.get("FocalLengthIn35mmFilm"))

    def numeric(value, expected):
        return type(value) in (int, float) and math.isclose(value, expected, rel_tol=0, abs_tol=1e-9)

    return {"actual": actual, "equivalent": equivalent, "numeric_correct": numeric(actual, 4.38) and numeric(equivalent, 27), "error": None}


def load(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def messages_for(results, arm):
    document = load(results / f"{arm}-trajectory.json", {})
    return [message for trajectory in document.get("trajectories") or [] for message in trajectory.get("messages") or []]


def first_request_attribution(evidence):
    requests = sorted(
        [
            event
            for event in evidence.get("events", [])
            if event.get("phase") == "request"
            and not event.get("subagent")
            and not event.get("tunnel")
            and (event.get("inference") or {}).get("agentRole") in (None, "", "main")
            and ((event.get("inference") or {}).get("toolCount") or (event.get("inference") or {}).get("tools"))
        ],
        key=lambda event: event["seq"],
    )
    return (requests[0].get("plugins") or {}).get("evolve", {}) if requests else {}


def metrics_for(results, state, arm):
    messages = messages_for(results, arm)
    session = state["sessions"][arm]
    calls = [call for message in messages for call in message.get("tool_calls") or []]
    evidence = load(results / f"{arm}-cortex-session.json", {})
    # Tool errors are the structured Cortex observations, not guesses from text.
    errors = {
        result.get("toolUseId")
        for event in evidence.get("events", [])
        for result in (event.get("inference") or {}).get("toolResults") or []
        if result.get("isError")
    }
    return {
        **metadata_check(results / "photos" / arm / "photo-result.json"),
        "tool_calls": len(calls),
        "tool_results": sum(message.get("role") == "tool" for message in messages),
        "observed_tool_errors": len(errors),
        "assistant_messages": sum(message.get("role") == "assistant" for message in messages),
        "elapsed_seconds": session.get("elapsed_seconds"),
        "first_request_evolve": first_request_attribution(evidence),
    }


def trajectory_html(results, arm):
    sections = []
    for index, message in enumerate(messages_for(results, arm), 1):
        role = message.get("role", "unknown")
        body = message.get("content") or ""
        if not isinstance(body, str):
            body = json.dumps(body, indent=2, ensure_ascii=False)
        if message.get("tool_calls"):
            body += "\n\nTool calls:\n" + json.dumps(message["tool_calls"], indent=2, ensure_ascii=False)
        sections.append(
            f'<details class="message"><summary>{index}. {esc(role)}{(" · " + esc(message.get("tool_call_id"))) if message.get("tool_call_id") else ""}</summary><pre>{esc(body)}</pre></details>'
        )
    return "\n".join(sections) or "<p>No converted messages were captured.</p>"


def build_report(results: Path, state):
    metrics = {arm: metrics_for(results, state, arm) for arm in ("baseline", "guided")}
    ids = metrics["guided"]["first_request_evolve"].get("ids") or []
    served = {str(row["id"]) for row in state.get("promoted", [])}
    injected = bool(metrics["guided"]["first_request_evolve"].get("injected") and ids and set(map(str, ids)).issubset(served))
    same_prompt = state["sessions"]["baseline"]["prompt"] == state["sessions"]["guided"]["prompt"]
    clean_baseline = not metrics["baseline"]["first_request_evolve"].get("injected")
    if not (injected and same_prompt and clean_baseline):
        verdict = "The run does not establish a valid Evolve comparison: check prompt equality and first-request attribution."
    elif metrics["guided"]["numeric_correct"] and not metrics["baseline"]["numeric_correct"]:
        verdict = "This pair improved numeric JSON correctness with reviewed Evolve guidelines injected."
    elif metrics["guided"]["numeric_correct"] and metrics["baseline"]["numeric_correct"]:
        verdict = "Both attempts produced correct numeric JSON. This pair shows guideline use, with no correctness gain."
    elif metrics["baseline"]["numeric_correct"]:
        verdict = "The baseline passed and the guided attempt failed numeric JSON correctness. This pair regressed."
    else:
        verdict = "Neither attempt produced correct numeric JSON. This pair shows no correctness improvement."
    summary = {
        "verdict": verdict,
        "reviewed_ids_injected": injected,
        "identical_prompts": same_prompt,
        "baseline_uninjected": clean_baseline,
        "metrics": metrics,
    }
    (results / "comparison.json").write_text(json.dumps(summary, indent=2) + "\n")
    rows = []
    for label, key in [
        ("Actual focal length (expected numeric 4.38)", "actual"),
        ("35 mm equivalent (expected numeric 27)", "equivalent"),
        ("Numeric JSON correctness", "numeric_correct"),
        ("Tool calls", "tool_calls"),
        ("Tool results", "tool_results"),
        ("Observed tool errors", "observed_tool_errors"),
        ("Assistant messages", "assistant_messages"),
        ("Elapsed seconds, including human approvals", "elapsed_seconds"),
    ]:
        cells = "".join(f"<td>{esc(json.dumps(metrics[arm][key]))}</td>" for arm in ("baseline", "guided"))
        rows.append(f"<tr><th>{esc(label)}</th>{cells}</tr>")
    guidelines = "".join(
        f"<article><p>Candidate {esc(row['candidate_id'])} → serving {esc(row['id'])}</p><blockquote>{esc(row['content'])}</blockquote><details><summary>Provenance and review metadata</summary><pre>{esc(json.dumps(row['metadata'], indent=2))}</pre></details></article>"
        for row in state.get("promoted", [])
    )
    outputs = "".join(
        f"<article><h3>{arm.title()}</h3><pre>{esc((results / 'photos' / arm / 'photo-result.json').read_text() if (results / 'photos' / arm / 'photo-result.json').exists() else 'No photo-result.json was saved.')}</pre></article>"
        for arm in ("baseline", "guided")
    )
    traces = "".join(
        f'<h3>{arm.title()} · {esc(state["sessions"][arm]["id"])}</h3><p><a href="{arm}-trajectory.json">Full converted trajectory JSON</a> · <a href="{arm}-cortex-session.json">Cortex events</a></p>{trajectory_html(results, arm)}'
        for arm in ("baseline", "training", "guided")
        if arm in state["sessions"]
    )
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cortex + Evolve photo comparison</title>
<style>body{{overflow-wrap:anywhere;font:16px/1.6 system-ui,sans-serif;max-width:1080px;margin:40px auto;padding:0 24px;color:#172033;background:#f8fafc}}h1,h2,h3{{line-height:1.2}}h2{{margin-top:44px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace;background:#eef2f6;padding:18px;border-radius:8px}}table{{width:100%;border-collapse:collapse;background:white}}td,th{{text-align:left;border-bottom:1px solid #ddd;padding:12px}}.verdict{{padding:22px;border-left:5px solid #0f62fe;background:#e8f0ff;font-size:20px}}.columns{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}article{{background:white;padding:20px;border-radius:8px;margin:12px 0}}blockquote{{margin:12px 0;padding-left:16px;border-left:3px solid #0f62fe}}details{{margin:10px 0}}summary{{cursor:pointer;font-weight:600}}svg{{max-width:100%;height:auto}}a{{color:#0043ce}}@media(max-width:650px){{.columns{{display:block}}}}</style>
<h1>Cortex + Evolve: local photo metadata</h1>
<p>Run created {esc(state["created_at"])}. Task model: {esc(state["model"])}. Reflection model: {esc(state["learning_model"])}.</p>
<p class="verdict">{esc(verdict)}</p>
<p>This is one paired demonstration. Responses can vary. Elapsed time includes terminal interaction and approvals; it is not an isolated model-speed metric.</p>
<h2>The task and learning boundary</h2>
<p>Both comparison sessions receive the same original JPEG and focal-length prompt. The stored tags are FocalLength = 4.38 mm and FocalLengthIn35mmFormat = 27 mm (Pillow/spec name: FocalLengthIn35mmFilm). The JSON must contain numbers at stored precision; ExifTool's formatted display can round 4.38 to “4.4 mm”.</p>
<pre>{esc(state["sessions"]["baseline"]["prompt"])}</pre>
<p>Evolve learns only from the separate GPS training session, {esc(state["sessions"].get("training", {}).get("id", "not captured"))}. The fixture has N/W references and signed coordinates about 51.4936083333, -0.0375611111. Baseline output and focal answers are excluded from learning. Each arm has a fresh Claude configuration and workspace. ExifTool, Python, Pillow, and the gateway are available equally to both comparison arms.</p>
<h2>Key pieces</h2>
<svg viewBox="0 0 980 300" role="img" aria-label="Claude Code sends requests through Cortex and a gateway bridge to an LLM. Cortex records trajectories and retrieves reviewed Evolve guidelines. Evolve learns candidates from the GPS trajectory, then a reviewer promotes them.">
<defs><marker id="a" markerWidth="8" markerHeight="8" refX="7" refY="4" orient="auto"><path d="M0,0 L8,4 L0,8" fill="#0f62fe"/></marker></defs>
<g fill="white" stroke="#9ab3d5" stroke-width="2"><rect x="10" y="25" width="190" height="65" rx="8"/><rect x="260" y="25" width="190" height="65" rx="8"/><rect x="510" y="25" width="190" height="65" rx="8"/><rect x="760" y="25" width="210" height="65" rx="8"/><rect x="250" y="175" width="240" height="75" rx="8"/><rect x="550" y="175" width="420" height="75" rx="8"/></g>
<g font-family="system-ui" font-size="17" fill="#172033" text-anchor="middle"><text x="105" y="65">Claude Code + tools</text><text x="355" y="65">Cortex + Evolve plugin</text><text x="605" y="65">Gateway bridge</text><text x="865" y="65">Task LLM API</text><text x="370" y="205">Full Evolve MCP</text><text x="370" y="231">+ reflection LLM</text><text x="760" y="205">photo-candidates → human review</text><text x="760" y="231">→ photo-reviewed</text></g>
<g stroke="#0f62fe" stroke-width="2" fill="none" marker-end="url(#a)"><path d="M200,58 H250"/><path d="M450,58 H500"/><path d="M700,58 H750"/><path d="M355,90 V165"/><path d="M330,175 V100"/><path d="M490,210 H540"/><path d="M580,175 V135 H395 V100"/></g>
<g font-family="system-ui" font-size="13" fill="#465770"><text x="365" y="122">GPS capture / retrieve</text><text x="590" y="128">reviewed guidelines injected on request</text><text x="12" y="290">Services and tools run in one container; only API calls leave it. Results are saved on the host.</text></g></svg>
<h2>Measured output</h2><table><thead><tr><th>Check</th><th>Baseline</th><th>Guided</th></tr></thead><tbody>{"".join(rows)}</tbody></table>
<p>The checker reads exact numeric values under the native EXIF keys. It does not grade the prose answer; review the assistant's tag interpretation below. Tool errors count structured isError observations, deduplicated by tool-use ID.</p>
<div class="columns">{outputs}</div>
<h2>Generated guidelines and promotion</h2><p><a href="photo-candidates.json">All candidates</a> · <a href="photo-promoted.json">Reviewed copies</a> · <a href="learning.json">Training processing result</a></p>{guidelines}
<p>Candidate storage is separate from the namespace Cortex serves. Promotion preserves the generated content and source metadata. The selection is frozen before the guided attempt.</p>
<h2>First-request attribution</h2><pre>{esc(json.dumps({"baseline": metrics["baseline"]["first_request_evolve"], "guided": metrics["guided"]["first_request_evolve"], "reviewed_ids_matched": injected, "identical_prompts": same_prompt}, indent=2))}</pre>
<p><a href="photo-retrieval.json">MCP retrieval preview</a>. Preview retrieval alone does not prove injection; the request event above must contain injected=true and the promoted serving IDs.</p>
<h2>Trajectories: before, training, and after</h2><p>Expand messages to inspect actual commands, arguments, output, corrections, and answers. JSON links also preserve system context and tool definitions. Cortex's parsed capture excludes image bytes and hidden reasoning; it sees tool output after Claude sends it in a later request.</p>{traces}
</html>"""
    (results / "comparison.html").write_text(document)
    return summary
