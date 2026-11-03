"""Run new task families through the existing isolated Cortex experiment driver.

Run from the repository root with uv run python -m
experiments.cortex_workflow_learning.run COMMAND. No everyday configuration is
modified. Evolve learns only from the explicitly selected training sessions.
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import yaml

from ..cortex_learning_demo import run as driver
from . import tasks

HERE = Path(__file__).resolve().parent
CURRENT_LABEL = None


def configure(directory):
    driver.HERE = HERE
    driver.ARTIFACTS = directory.resolve()
    driver.STATE = driver.ARTIFACTS / "state.json"
    driver.CASES = {case: spec for case, spec in tasks.CASES.items() if spec["variant"] != "train"}
    driver.SOURCE = "Synthetic billing workflows: CSV exports, duplicate-charge refunds, and revenue reconciliation"
    driver.PROMPT = "Reconcile monthly production-account revenue from the local billing ledger according to its accounting contract."
    driver.TASK_TOOL = "ops.py"
    driver.PROFILE = "workflow-demo"
    driver.LEARN_NS = "workflow-candidates"
    driver.SERVE_NS = "workflow-reviewed"
    driver.REVIEW_PURPOSE = "reusable billing API and revenue reconciliation procedure"
    driver.EVALUATION_ARMS = ("baseline", "evolve")
    driver.prepare_trial = prepare
    driver.decode_answer = decode_answer
    driver.grade = grade_current


def prepare(label, case):
    target = driver.ARTIFACTS / "trials" / label
    if target.exists():
        raise RuntimeError(f"Trial already exists: {label}")
    target.mkdir(parents=True)
    shutil.copy2(HERE / "ops.py", target / "ops.py")
    (target / "service-state.json").write_text(json.dumps(tasks.CASES[case]["state"], indent=2) + "\n")
    settings = {"env": {"HTTPS_PROXY": driver.PROXY, "NODE_EXTRA_CA_CERTS": str(driver.CA_DIR / "ca.crt")}}
    (target / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    return target


def decode_answer(text):
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        if isinstance(value, dict) and ("file" in value or "refunded_charge_ids" in value or "totals_minor" in value):
            return value
    return None


def grade_current(case, answer):
    return tasks.grade(case, answer, driver.ARTIFACTS / "trials" / CURRENT_LABEL)


def trial(label, case, arm, *, feedback=False, via_bridge=False):
    global CURRENT_LABEL
    CURRENT_LABEL = label
    driver.INFERENCE_BASE_URL = "http://127.0.0.1:18202" if via_bridge else None
    spec = tasks.CASES[case]
    if (arm == "training") != (spec["variant"] == "train"):
        raise ValueError("Use only *_train cases for training, and separate inputs for evaluation")
    prompt = spec["prompt"]
    resume = None
    if feedback:
        previous = next(trial for trial in reversed(driver.read_state()["trials"]) if trial["label"] == label)
        prompt = (
            "Independent validation of your previous work returned these checks: "
            + json.dumps(previous["checks"])
            + ". Inspect the actual service state and complete the original task. Explain the reusable procedure and return the required JSON."
        )
        resume = previous["session_id"]
    result = driver.run_trial(label, case, arm, prompt=prompt, resume=resume)
    directory = driver.ARTIFACTS / "trials" / label
    calls_path = directory / "service.calls.jsonl"
    calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
    result["family"] = spec["family"]
    result["transport"] = "gateway-bridge" if via_bridge else "direct-gateway"
    result["service_calls"] = len(calls)
    result["service_errors"] = sum(not call["ok"] for call in calls)
    state = driver.read_state()
    state["trials"][-1] = result
    driver.write_state(state)
    suffix = "feedback" if feedback else "initial"
    (directory / f"{suffix}.metrics.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: result[key] for key in ("passed", "checks", "service_calls", "service_errors", "bash_commands")}, indent=2))


def learn(label, mode, objective):
    state = driver.read_state()
    metric = next(trial for trial in reversed(state["trials"]) if trial["label"] == label)
    if tasks.CASES[metric["case"]]["variant"] != "train":
        raise ValueError("Learning from evaluation cases is forbidden")
    env = driver.base_env(state["model"])
    env["EVOLVE_GUIDELINES_MODE"] = mode
    os.environ.update(env)
    from altk_evolve.processing import Trajectory, TrajectoryOutcome
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory
    from altk_evolve.sync.cortex_sync import CortexCursorStore, CortexSessionClient, ingest_session

    cortex = CortexSessionClient(driver.SESSION_API)
    try:
        captured = ingest_session(
            None,
            cortex,
            metric["session_id"],
            namespace_id=driver.LEARN_NS,
            cursors=CortexCursorStore(driver.ARTIFACTS / "runtime" / "training-cursors.json"),
            min_new_turns=1,
            dry_run=True,
        )
    finally:
        cortex.close()
    if captured.trajectory is None:
        raise RuntimeError(f"Training trajectory unavailable: {captured.status}")
    trajectory = Trajectory.model_validate(captured.trajectory)
    checks = dict(metric["checks"])
    detail = {"task_correctness_passed": metric["passed"], "checks": checks, "expected": metric["expected"]}
    if objective == "fewer-errors":
        checks["no_rejected_api_calls"] = metric["service_errors"] == 0
        calls = [json.loads(line) for line in (driver.ARTIFACTS / "trials" / label / "service.calls.jsonl").read_text().splitlines()]
        detail["quality_objective"] = "Complete the task without rejected API invocations; task correctness is a separate criterion."
        detail["rejected_api_calls"] = [call for call in calls if not call["ok"]]
        trajectory.batch = trajectory.batch.model_copy(update={"revision": "2"})
    if mode == "consistency":
        # A changed analysis is a new revision; redelivery of the original batch
        # correctly skips processors even if deployment defaults have changed.
        trajectory.batch = trajectory.batch.model_copy(update={"revision": "3"})
    trajectory.outcome = TrajectoryOutcome(
        success=all(checks.values()),
        failed_checks=tuple(name for name, passed in checks.items() if not passed),
        detail=json.dumps(detail),
    )
    output = driver.ARTIFACTS / f"learning-{label}-{mode}-{objective}"
    output.with_suffix(".input.json").write_text(json.dumps(trajectory.model_dump(mode="json"), indent=2) + "\n")
    rendered = parse_openai_agents_trajectory(trajectory.messages)
    output.with_suffix(".rendered.txt").write_text(rendered["trajectory_summary"] + "\n")
    calls = [call for message in trajectory.messages for call in message.get("tool_calls", [])]
    results = [message for message in trajectory.messages if message.get("role") == "tool"]
    call_ids = {call["id"] for call in calls}
    recording = {
        "tool_calls": len(calls),
        "tool_results": len(results),
        "paired_results": sum(message.get("tool_call_id") in call_ids for message in results),
        "rendered_observations": rendered["trajectory_summary"].count("Tool result (observed output):"),
    }
    client = EvolveClient()
    client.ensure_namespace(driver.LEARN_NS)
    result = client.process_trajectory(trajectory, namespace_id=driver.LEARN_NS)
    report = {
        "label": label,
        "mode": mode,
        "objective": objective,
        "task_correctness_passed": metric["passed"],
        "quality_passed": all(checks.values()),
        "updates": result.updates,
        "skipped_processors": result.skipped_processors,
        "recording": recording,
    }
    output.with_suffix(".result.json").write_text(json.dumps(report, indent=2) + "\n")
    state["learning"] = [*state.get("learning", []), report]
    driver.write_state(state)
    print(json.dumps(report, indent=2))


def bridge_start():
    state = driver.read_state()
    if "bridge" in state["services"]:
        raise RuntimeError("The bridge has already been started")
    env = driver.base_env(state["model"])
    env["WORKFLOW_REAL_GATEWAY"] = env["ANTHROPIC_BASE_URL"]
    command = ["uv", "run", "--no-sync", "python", "-m", "experiments.cortex_workflow_learning.bridge"]
    with (driver.ARTIFACTS / "runtime" / "bridge.log").open("w") as log:
        process = subprocess.Popen(command, cwd=driver.REPO, env=env, stdout=log, stderr=log, start_new_session=True)
    state["services"]["bridge"] = {"pid": process.pid, "command": command}
    driver.write_state(state)
    print(json.dumps({"started": "bridge", "port": 18202}))


def set_injection(enabled):
    path = driver.ARTIFACTS / "runtime" / "cortex.yaml"
    config = yaml.safe_load(path.read_text())
    plugin = next(plugin for plugin in config["pipeline"]["outbound"]["plugins"] if plugin["name"] == "evolve")
    plugin["config"]["inject"] = enabled
    if plugin["config"]["learn"]:
        raise RuntimeError("Learning must stay disabled during evaluation")
    path.write_text(yaml.safe_dump(config, sort_keys=False))
    time.sleep(3)
    print(json.dumps({"inject": enabled, "learn": False}))


def summary():
    state = driver.read_state()
    report = {
        "model": state["model"],
        "scope": driver.SOURCE,
        "learning": state.get("learning", []),
        "promoted": state.get("promoted", []),
        "transports": {},
    }
    for transport in ("direct-gateway", "gateway-bridge"):
        report["transports"][transport] = {}
        for family in ("export", "refund", "ledger"):
            report["transports"][transport][family] = {}
            for arm in ("baseline", "evolve"):
                trials = [
                    trial
                    for trial in state["trials"]
                    if trial["family"] == family and trial["arm"] == arm and trial.get("transport", "direct-gateway") == transport
                ]
                report["transports"][transport][family][arm] = {
                    "passed": sum(trial["passed"] for trial in trials),
                    "trials": len(trials),
                    "agent_turns": sum(trial["num_turns"] for trial in trials),
                    "service_calls": sum(trial["service_calls"] for trial in trials),
                    "service_errors": sum(trial["service_errors"] for trial in trials),
                    "input_tokens": sum((trial["usage"] or {}).get("input_tokens", 0) for trial in trials),
                    "output_tokens": sum((trial["usage"] or {}).get("output_tokens", 0) for trial in trials),
                    "trials_with_token_usage": sum(trial["usage"] is not None for trial in trials),
                    "injection_matches_arm": bool(trials) and all(bool(trial["injected_ids"]) == (arm == "evolve") for trial in trials),
                    "details": [
                        {
                            "timed_out": bool(trial.get("timed_out", False)),
                            "turn_count_source": trial.get("turn_count_source", "claude_result"),
                            **{
                                key: trial[key]
                                for key in (
                                    "label",
                                    "case",
                                    "passed",
                                    "checks",
                                    "answer",
                                    "injected_ids",
                                    "service_calls",
                                    "service_errors",
                                    "num_turns",
                                )
                            },
                        }
                        for trial in trials
                    ],
                }
    probe = driver.ARTIFACTS / "system-block-probe.json"
    if probe.exists():
        report["gateway_system_block_probe"] = json.loads(probe.read_text())
    for name in ("recording-audit", "comparison-plan"):
        path = driver.ARTIFACTS / f"{name}.json"
        if path.exists():
            report[name.replace("-", "_")] = json.loads(path.read_text())
    excluded = driver.ARTIFACTS / "excluded-network-attempts.json"
    if excluded.exists():
        report["excluded_network_attempts"] = json.loads(excluded.read_text())
    example = driver.ARTIFACTS / "positive-refund-example.json"
    if example.exists():
        report["selected_positive_case"] = {"case": "refund_f", "selection": "posthoc", "evidence_file": str(example)}
    (driver.ARTIFACTS / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    compact = {
        transport: {
            family: {arm: {key: value for key, value in metrics.items() if key != "details"} for arm, metrics in arms.items()}
            for family, arms in families.items()
            if any(metrics["trials"] for metrics in arms.values())
        }
        for transport, families in report["transports"].items()
        if any(metrics["trials"] for arms in families.values() for metrics in arms.values())
    }
    print(json.dumps({"model": report["model"], "transports": compact, "full_summary": str(driver.ARTIFACTS / "summary.json")}, indent=2))


def compare(cases):
    """Freeze selection and alternate arm order before any held-out results exist."""
    if any(tasks.CASES[case]["variant"] == "train" for case in cases):
        raise ValueError("Training cases cannot enter the comparison")
    state = driver.read_state()
    plan = {
        "cases": cases,
        "guidelines": state["promoted"],
        "selection": "Reviewed from training only, before any held-out evaluation",
        "primary": "Independent exact report correctness",
        "secondary": "Agent turns and rejected service calls; report both even if worse",
        "order": [
            {"case": case, "arms": ["baseline", "evolve"] if index % 2 == 0 else ["evolve", "baseline"]} for index, case in enumerate(cases)
        ],
    }
    path = driver.ARTIFACTS / "comparison-plan.json"
    if path.exists():
        if json.loads(path.read_text()) != plan:
            raise RuntimeError("The frozen cases and guidelines cannot change during evaluation")
    else:
        if any(trial["arm"] in ("baseline", "evolve") for trial in state["trials"]):
            raise RuntimeError("Use fresh artifacts for a predeclared comparison")
        path.write_text(json.dumps(plan, indent=2) + "\n")
    completed = {trial["label"] for trial in state["trials"]}
    for item in plan["order"]:
        for arm in item["arms"]:
            if f"{arm}-{item['case']}" in completed:
                continue
            set_injection(arm == "evolve")
            trial(f"{arm}-{item['case']}", item["case"], arm, via_bridge=True)
    summary()


def verify():
    """Regrade saved evaluation artifacts without a model, credentials, or services."""
    reports = []
    for path in sorted((driver.ARTIFACTS / "trials").glob("*/initial.metrics.json")):
        metric = json.loads(path.read_text())
        if metric["arm"] not in ("baseline", "evolve"):
            continue
        result = tasks.grade(metric["case"], metric["answer"], path.parent)
        passed = result["passed"] and not metric.get("timed_out", False)
        matches = passed == metric["passed"] and all(metric["checks"].get(key) == value for key, value in result["checks"].items())
        reports.append({"label": metric["label"], "passed": passed, "matches_saved_grade": matches})
    (driver.ARTIFACTS / "verification.json").write_text(json.dumps(reports, indent=2) + "\n")
    print(json.dumps({"regraded": len(reports), "all_match": all(report["matches_saved_grade"] for report in reports)}, indent=2))
    if not reports or not all(report["matches_saved_grade"] for report in reports):
        raise RuntimeError("Evaluation artifacts are missing or no longer match the independent grade")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=HERE / "artifacts")
    parser.add_argument(
        "command",
        choices=[
            "preflight",
            "start",
            "bridge-start",
            "injection-on",
            "injection-off",
            "trial",
            "compare",
            "verify",
            "feedback",
            "learn",
            "candidates",
            "promote",
            "warm",
            "summary",
            "stop",
        ],
    )
    parser.add_argument("--label")
    parser.add_argument("--case", choices=tasks.CASES)
    parser.add_argument("--cases", nargs="+", choices=tasks.CASES, default=[f"ledger_{variant}" for variant in "abcdef"])
    parser.add_argument("--arm", choices=["training", "baseline", "evolve"])
    parser.add_argument("--mode", choices=["standard", "consistency"], default="standard")
    parser.add_argument("--objective", choices=["correctness", "fewer-errors"], default="correctness")
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--via-bridge", action="store_true")
    args = parser.parse_args()
    configure(args.artifacts_dir)
    if args.command == "preflight":
        driver.preflight()
    elif args.command == "start":
        driver.start()
    elif args.command == "trial":
        trial(args.label, args.case, args.arm, via_bridge=args.via_bridge)
    elif args.command == "compare":
        compare(args.cases)
    elif args.command == "verify":
        verify()
    elif args.command == "bridge-start":
        bridge_start()
    elif args.command == "injection-on":
        set_injection(True)
    elif args.command == "injection-off":
        set_injection(False)
    elif args.command == "feedback":
        previous = next(trial for trial in reversed(driver.read_state()["trials"]) if trial["label"] == args.label)
        trial(args.label, previous["case"], "training", feedback=True, via_bridge=args.via_bridge)
    elif args.command == "learn":
        learn(args.label, args.mode, args.objective)
    elif args.command == "candidates":
        driver.candidates()
    elif args.command == "promote":
        driver.promote(args.ids)
    elif args.command == "warm":
        asyncio.run(driver.warm())
    elif args.command == "summary":
        summary()
    elif args.command == "stop":
        driver.stop()


if __name__ == "__main__":
    main()
