"""Separate guideline quality from delivery: fixed guidelines, interleaved arms, same tasks.

seed: store a hand-written working-directory guideline, and optionally the learned
guidelines of an earlier experiment, each in its own serving namespace.
run: interleave the chosen arms in rounds, rotating packets; capture each session and
record where the bridge saw the guideline block in the payload sent to the gateway.
report (evaluator container): grade outputs and count wrong-path file reads per arm.
"""

import argparse
import json
import shutil
import statistics
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RUNS = "placement-runs.json"
HAND_NAMESPACE = "invoice-placement-hand"
LEARNED_NAMESPACE = "invoice-placement-learned"
# Written for the error every invoice experiment showed: Read on an invented /tmp/tmp.* path.
HAND_GUIDELINE = (
    "Relative paths in a task, such as input/, are relative to the CWD stated in your environment. "
    "Build absolute file paths from that CWD (or from pwd) before reading files; "
    "never guess a temporary directory such as /tmp/tmp.*."
)
ARMS = {
    "baseline": {"inject": False, "namespace": HAND_NAMESPACE, "placement": None},
    "system": {"inject": True, "namespace": HAND_NAMESPACE, "placement": None},
    "user": {"inject": True, "namespace": HAND_NAMESPACE, "placement": "user"},
    "learned-system": {"inject": True, "namespace": LEARNED_NAMESPACE, "placement": None},
    "learned-user": {"inject": True, "namespace": LEARNED_NAMESPACE, "placement": "user"},
}
PACKETS = ("trial-1", "trial-2", "trial-3")
TIMEOUT_SECONDS = 600


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def seed():
    import services
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    import driver

    client = EvolveClient()
    sets = {HAND_NAMESPACE: [{"content": HAND_GUIDELINE, "origin": "hand-written"}]}
    learned = read(services.RESULTS / "learned-guidelines.json")
    if learned:
        sets[LEARNED_NAMESPACE] = [{"content": row["content"], "origin": row.get("origin", "learned")} for row in learned]
    seeded = {}
    for namespace, rows in sets.items():
        client.ensure_namespace(namespace)
        if client.get_all_entities(namespace, filters={"type": "guideline"}, limit=10):
            raise RuntimeError(f"{namespace} is already seeded; use a fresh results directory.")
        entities = [
            Entity(
                type="guideline",
                content=row["content"],
                # Retrieval ranks by source-task similarity; the task is the one evaluated.
                metadata={"status": "active", "origin": row["origin"], "task_description": driver.prompt(), "source_task_id": namespace},
            )
            for row in rows
        ]
        updates = client.update_entities(namespace, entities, enable_conflict_resolution=False)
        seeded[namespace] = [{"id": u.id, "content": e.content, "origin": e.metadata["origin"]} for u, e in zip(updates, entities)]
    services.write_json(services.RESULTS / "placement-guidelines.json", seeded)
    print(json.dumps(seeded, indent=2))


def delivery_rows(services, session):
    path = services.RUNTIME / "bridge-delivery.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows if row.get("session") == session]


def run_one(arm, packet, label):
    import demo as shared
    import services

    import driver

    spec = ARMS[arm]
    services.health()
    services.SERVE_NAMESPACE = spec["namespace"]
    services.set_injection(spec["inject"], spec["placement"])
    state = services.load_state()
    if label in state["sessions"]:
        raise RuntimeError(f"{label} already exists; evidence is not overwritten.")
    session = str(uuid.uuid4())
    target = services.RESULTS / "tasks" / label
    config = services.RUNTIME / "claude-config" / label
    target.mkdir(parents=True)
    shutil.copytree(ROOT / "fixtures" / packet, target / "input")
    shared.prepare_config(config)
    state["sessions"][label] = {
        "id": session,
        "packet": packet,
        "prompt": driver.prompt(),
        "arm": arm,
        "arm_config": spec,
        "started_at": datetime.now(UTC).isoformat(),
        "input_hashes_before": driver.hashes(target / "input"),
        "harness": driver.harness(True),
    }
    services.save_state(state)
    command = [*shared.claude_command(session), *driver.BATCH_FLAGS, driver.prompt()]
    started = time.monotonic()
    with (
        (services.RESULTS / f"{label}-cli-1.json").open("w") as stdout,
        (services.RESULTS / f"{label}-cli-1.stderr.txt").open("w") as stderr,
    ):
        try:
            code = subprocess.run(
                command,
                cwd=target,
                env=shared.claude_environment(config),
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=TIMEOUT_SECONDS,
            ).returncode
        except subprocess.TimeoutExpired:
            code = 124
    state = services.load_state()
    state["sessions"][label].update(exit_code=code, elapsed_seconds=round(time.monotonic() - started, 2))
    services.save_state(state)
    driver.capture(label)
    return {
        "label": label,
        "arm": arm,
        "packet": packet,
        "session": session,
        "exit_code": code,
        "delivery": delivery_rows(services, session),
    }


def run(arms, count):
    import services

    unknown = [arm for arm in arms if arm not in ARMS]
    if unknown:
        raise RuntimeError(f"Unknown arms: {unknown}. Choose from {sorted(ARMS)}.")
    if not services.load_state().get("checked_at"):
        raise RuntimeError("Run ./demo setup first.")
    path = services.RESULTS / RUNS
    runs = read(path, [])
    done = {arm: sum(row["arm"] == arm for row in runs) for arm in arms}
    for round_number in range(count):
        packet = PACKETS[round_number % len(PACKETS)]
        # Rotate arm order each round so no arm always runs first.
        order = arms[round_number % len(arms) :] + arms[: round_number % len(arms)]
        for arm in order:
            done[arm] += 1
            label = f"{arm}-{done[arm]}"
            print(f"Round {round_number + 1}/{count}: {label} on {packet}", flush=True)
            runs.append(run_one(arm, packet, label))
            services.write_json(path, runs)
    services.set_injection(False)


def wrong_path_reads(messages, cwd):
    calls = [call for message in messages if message.get("role") == "assistant" for call in message.get("tool_calls") or []]
    paths = []
    for call in calls:
        if call["function"]["name"] != "Read":
            continue
        try:
            file_path = json.loads(call["function"]["arguments"]).get("file_path", "")
        except ValueError:
            continue
        if not file_path.startswith(cwd + "/"):
            paths.append(file_path)
    return paths


def report(results):
    import evaluate

    state = evaluate.load(results / "state.json")
    oracle = evaluate.load(ROOT / "oracle/reference.json")
    rows = []
    for run_row in evaluate.load(results / RUNS, []):
        label = run_row["label"]
        if not evaluate.completed(state, label):
            rows.append({**run_row, "completed": False})
            continue
        measured = evaluate.metrics(results, state, label, oracle)
        wrong = wrong_path_reads(evaluate.messages_for(results, label), f"/results/tasks/{label}")
        attribution = measured["first_request_evolve"]
        delivered = run_row["delivery"][0] if run_row["delivery"] else {}
        rows.append(
            {
                **{key: run_row[key] for key in ("label", "arm", "packet", "session")},
                "completed": True,
                "all_correct": measured["all_correct"],
                "tool_calls": measured["tool_calls"],
                "tool_errors": measured["observed_tool_errors"],
                "wrong_path_reads": len(wrong),
                "wrong_paths": wrong,
                "injected_ids": attribution.get("ids") if attribution.get("injected") else [],
                "delivered_in_system": delivered.get("in_system"),
                "delivered_in_first_user": delivered.get("in_first_user"),
            }
        )
    arms = {}
    for row in rows:
        arms.setdefault(row["arm"], []).append(row)
    summary = {}
    for arm, group in arms.items():
        done = [row for row in group if row["completed"]]
        calls = [row["tool_calls"] for row in done]
        summary[arm] = {
            "runs": len(group),
            "completed": len(done),
            "correct": sum(row["all_correct"] for row in done),
            "runs_with_wrong_path_reads": sum(row["wrong_path_reads"] > 0 for row in done),
            "mean_tool_calls": round(statistics.mean(calls), 1) if calls else None,
            "median_tool_calls": statistics.median(calls) if calls else None,
            "mean_tool_errors": round(statistics.mean(row["tool_errors"] for row in done), 2) if done else None,
            "delivered_runs": sum(bool(row["delivered_in_system"] or row["delivered_in_first_user"]) for row in done),
        }
    output = {"model": state["model"], "summary": summary, "runs": rows}
    (results / "placement-summary.json").write_text(json.dumps(output, indent=2) + "\n")
    print("| Arm | Runs | Correct | Runs with wrong-path reads | Mean calls | Median calls | Mean tool errors | Guideline delivered |")
    print("|---|---:|---:|---:|---:|---:|---:|---:|")
    for arm, row in summary.items():
        print(
            f"| {arm} | {row['completed']}/{row['runs']} | {row['correct']} | {row['runs_with_wrong_path_reads']} | "
            f"{row['mean_tool_calls']} | {row['median_tool_calls']} | {row['mean_tool_errors']} | {row['delivered_runs']} |"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "run", "report"))
    parser.add_argument("arms", nargs="*")
    parser.add_argument("--count", type=int, default=8, help="Rounds; each round runs every arm once on one packet.")
    parser.add_argument("--results", type=Path, default=Path("/results"))
    args = parser.parse_args()
    if args.command == "report":
        report(args.results)
        return
    import services

    services.configure_process()
    if args.command == "seed":
        seed()
    else:
        run(args.arms or ["baseline", "system"], args.count)


if __name__ == "__main__":
    main()
