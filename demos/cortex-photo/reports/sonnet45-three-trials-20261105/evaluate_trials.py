"""Evaluate final native EXIF values and render the recorded paired trials."""

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import report


def metadata_check(path):
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {"actual": None, "equivalent": None, "numeric_correct": False, "error": "Missing or invalid JSON"}
    found = {"actual": [], "equivalent": []}
    paths = {"actual": [], "equivalent": []}

    def visit(value, prefix=""):
        if isinstance(value, dict):
            for key, item in value.items():
                target = (
                    "actual"
                    if key == "FocalLength"
                    else "equivalent"
                    if key in ("FocalLengthIn35mmFormat", "FocalLengthIn35mmFilm")
                    else None
                )
                if target:
                    found[target].append(item)
                    paths[target].append(prefix + "/" + key)
                visit(item, prefix + "/" + key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, prefix + "/" + str(index))

    visit(data)

    def matches(values, expected):
        return bool(values) and all(type(v) in (float, int) and math.isclose(v, expected, rel_tol=0, abs_tol=1e-9) for v in values)

    def display(values):
        return values[0] if len(values) == 1 else values or None

    return {
        "actual": display(found["actual"]),
        "equivalent": display(found["equivalent"]),
        "numeric_correct": matches(found["actual"], 4.38) and matches(found["equivalent"], 27),
        "tag_paths": paths,
        "error": None,
    }


def render_pair(root):
    state = json.loads((root / "state.json").read_text())
    state["run_notes"] = [
        "Both focal arms are fresh live Sonnet 4.5 sessions. The GPS recording and two reviewed guidelines are reused unchanged; no new learning or corrective coaching occurred.",
        "The numeric checker accepts exact native EXIF keys inside JSON wrappers, since the prompt does not require a top-level shape. It still requires numeric 4.38 and numeric 27, and rejects strings. The JSON summary records the tag paths.",
        "Elapsed time includes terminal approval and an interruption across the first baseline; it is excluded from efficiency comparisons.",
    ]
    original = report.metadata_check
    report.metadata_check = metadata_check
    try:
        return report.build_report(root, state)
    finally:
        report.metadata_check = original


def main():
    batch = Path(__file__).parent
    results = []
    for number in (1, 2, 3):
        root = batch / f"trial-{number}"
        state = json.loads((root / "state.json").read_text())
        if all(state["sessions"].get(arm, {}).get("captured") for arm in ("baseline", "guided")):
            results.append({"trial": number, **render_pair(root)})
    summary = {"task_model": "aws/claude-sonnet-4-5", "completed_pairs": len(results), "pairs": results}
    if results:
        summary["totals"] = {
            arm: {
                "correct_numeric_exports": sum(pair["metrics"][arm]["numeric_correct"] for pair in results),
                "tool_calls": sum(pair["metrics"][arm]["tool_calls"] for pair in results),
                "assistant_messages": sum(pair["metrics"][arm]["assistant_messages"] for pair in results),
            }
            for arm in ("baseline", "guided")
        }
    (batch / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
