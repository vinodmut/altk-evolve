"""Invoice sessions and explicit Evolve learning/review/promotion."""

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import time
import uuid
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

import demo as shared
import services
from report import first_request_attribution

ROOT = Path(__file__).resolve().parent
CANDIDATES = "invoice-candidates"
REVIEWED = "invoice-reviewed"


def prompt():
    return (ROOT / "prompt.txt").read_text().strip()


def label_for(arm, number=None, repeat=None):
    if arm == "training":
        if repeat is not None:
            raise RuntimeError("Training cannot be repeated in a frozen experiment.")
        return arm
    if arm not in ("baseline", "guided") or number not in (1, 2, 3):
        raise RuntimeError("baseline and guided require a trial number: 1, 2, or 3")
    if repeat is not None and repeat < 2:
        raise RuntimeError("Repeat numbers start at 2; the original run is repetition 1.")
    return f"{arm}-{number}" + (f"-repeat-{repeat}" if repeat is not None else "")


BATCH_FLAGS = [
    "--print",
    "--allowedTools",
    "Bash,Read,Write,Edit",
    "--permission-mode",
    "dontAsk",
    "--permission-prompts",
    "none",
    "--output-format",
    "json",
]
CONTINUATION = "Continue the original invoice task from this session and finish the requested outputs."


def harness(batch):
    return {
        "mode": "noninteractive" if batch else "interactive",
        "flags": BATCH_FLAGS if batch else [],
        "tools": ["Bash", "Read", "Write", "Edit"],
    }


def hashes(directory):
    return {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(directory.glob("*.pdf"))}


def check():
    services.health()
    for executable in ("pdftotext", "pdftoppm", "pdfinfo", "tesseract"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing installed tool: {executable}")
    config = services.RUNTIME / "claude-config" / ("check-" + str(uuid.uuid4()))
    shared.prepare_config(config)
    env = shared.claude_environment(config)
    probe = subprocess.run(
        [*shared.claude_command(), "-p", "Reply exactly INVOICE_READY. Do not use tools."],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode or "INVOICE_READY" not in probe.stdout:
        raise RuntimeError("Claude's live API check failed. Correct the private .env before starting tasks.")
    shared.check_reflection(env)
    state = services.load_state()
    state["checked_at"] = datetime.now(UTC).isoformat()
    state["task_family"] = "invoice-extraction"
    state["trial_order"] = ["baseline-1", "guided-1", "guided-2", "baseline-2", "baseline-3", "guided-3"]
    services.save_state(state)
    print(
        json.dumps(
            {"claude": "live API OK", "evolve": "live API OK", "model": state["model"], "learner": state["learning_model"]}, indent=2
        )
    )


def capture(label):
    from altk_evolve.sync.cortex_sync import CortexCursorStore, CortexSessionClient, ingest_session

    state = services.load_state()
    session = state["sessions"].get(label)
    if not session:
        raise RuntimeError(f"No session: {label}")
    with httpx.Client(trust_env=False, timeout=30) as client:
        response = client.get(services.API + f"/v1/sessions/{session['id']}", params={"limit": 2000})
        response.raise_for_status()
        evidence = response.json()
    services.write_json(services.RESULTS / f"{label}-cortex-session.json", evidence)
    cortex = CortexSessionClient(services.API)
    try:
        converted = ingest_session(
            None,
            cortex,
            session["id"],
            namespace_id=CANDIDATES,
            cursors=CortexCursorStore(services.RUNTIME / "export-cursors.json"),
            min_new_turns=1,
            dry_run=True,
        )
    finally:
        cortex.close()
    if not converted.trajectories:
        raise RuntimeError("No main-agent trajectory captured.")
    services.write_json(services.RESULTS / f"{label}-trajectory.json", asdict(converted))
    session.update(captured=True, first_request_evolve=first_request_attribution(evidence))
    session["input_hashes_after"] = hashes(services.RESULTS / "tasks" / label / "input")
    services.save_state(state)
    calls = sum(len(message.get("tool_calls") or []) for trajectory in converted.trajectories for message in trajectory["messages"])
    print(
        json.dumps(
            {"session": label, "id": session["id"], "tool_calls": calls, "first_request_evolve": session["first_request_evolve"]}, indent=2
        )
    )


async def retrieve(label):
    from fastmcp import Client

    async with Client(services.MCP) as client:
        result = await client.call_tool("get_guidelines_with_attribution", {"task": prompt(), "namespace_id": REVIEWED}, timeout=90)
    payload = json.loads("\n".join(block.text for block in result.content if hasattr(block, "text")))
    services.write_json(services.RESULTS / f"{label}-retrieval.json", payload)
    if not payload.get("entity_ids") or not payload.get("text"):
        raise RuntimeError("No reviewed invoice guidelines were retrieved.")
    print("Reviewed retrieval:", payload["entity_ids"], flush=True)


def task(arm, number=None, resume=False, batch=False, repeat=None):
    services.health()
    state = services.load_state()
    if not state.get("checked_at"):
        raise RuntimeError("Run ./demo setup first.")
    label = label_for(arm, number, repeat)
    target = services.RESULTS / "tasks" / label
    config = services.RUNTIME / "claude-config" / label
    if arm == "guided" and not state.get("promoted"):
        raise RuntimeError("Review and promote generated guidelines first.")
    services.set_injection(arm == "guided")
    if arm == "guided":
        asyncio.run(retrieve(label))
    if resume:
        if label not in state["sessions"] or (arm == "training" and state.get("learned")):
            raise RuntimeError("Resume requires an existing session; learned training is frozen.")
        if arm != "training" and state["sessions"][label].get("exit_code") == 0:
            raise RuntimeError("Completed evaluation sessions are frozen.")
        session = state["sessions"][label]["id"]
        command = [*shared.claude_command(), "--resume", session]
        continuation = (services.RESULTS / "training-feedback.txt").read_text().strip() if arm == "training" else CONTINUATION
        if batch:
            command.extend([*BATCH_FLAGS, continuation])
    else:
        if label in state["sessions"]:
            raise RuntimeError(f"{label} already exists. Capture it or start a fresh results directory; evidence is not overwritten.")
        session = str(uuid.uuid4())
        packet = "training" if arm == "training" else f"trial-{number}"
        if repeat is not None:
            plan = state.setdefault(
                "repetition_plan",
                {
                    "registered_at": datetime.now(UTC).isoformat(),
                    "packet": packet,
                    "repetitions": [1],
                    "additional_order": [],
                    "reason": "User-selected same-input follow-up; no relearning or changes to reviewed guidelines.",
                },
            )
            if plan["packet"] != packet:
                raise RuntimeError("Use the same packet for this experiment's repetition group.")
            plan["repetitions"] = sorted(set([*plan["repetitions"], repeat]))
            if label not in plan["additional_order"]:
                plan["additional_order"].append(label)
        shutil.copytree(ROOT / "fixtures" / packet, target / "input")
        shared.prepare_config(config)
        state["sessions"][label] = {
            "id": session,
            "packet": packet,
            "prompt": prompt(),
            "started_at": datetime.now(UTC).isoformat(),
            "input_hashes_before": hashes(target / "input"),
            "harness": harness(batch),
            "repetition": repeat or 1,
        }
        services.save_state(state)
        command = [*shared.claude_command(session), *(BATCH_FLAGS if batch else []), prompt()]
    session_state = state["sessions"][label]
    session_state.setdefault("harness", harness(False))
    launches = session_state.setdefault("launches", [])
    launches.append({"started_at": datetime.now(UTC).isoformat(), "harness": harness(batch), "resume": resume})
    if resume and batch:
        launches[-1]["continuation_prompt"] = continuation
    if resume and harness(batch) != session_state["harness"]:
        session_state["harness_transition"] = True
    services.save_state(state)
    mode = "Noninteractive" if batch else "Interactive"
    print(f"{mode} Claude Code: {label}, UUID {session}.", flush=True)
    started = time.monotonic()
    try:
        if batch:
            attempt = len(launches)
            output = services.RESULTS / f"{label}-cli-{attempt}.json"
            error = services.RESULTS / f"{label}-cli-{attempt}.stderr.txt"
            with output.open("w") as stdout, error.open("w") as stderr:
                code = subprocess.run(
                    command, cwd=target, env=shared.claude_environment(config), stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr
                ).returncode
        else:
            print("Approve local tools normally; enter /exit after the answer.", flush=True)
            code = subprocess.run(command, cwd=target, env=shared.claude_environment(config)).returncode
    except KeyboardInterrupt:
        code = 130
    state = services.load_state()
    previous = state["sessions"][label].get("elapsed_seconds", 0)
    state["sessions"][label].update(exit_code=code, elapsed_seconds=round(previous + time.monotonic() - started, 2))
    services.save_state(state)
    capture(label)
    print(f"Saved outputs: tasks/{label}/items.csv and reconciliation.csv. Use ./demo feedback for independent training checks.")


def candidates():
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    rows = [
        {"id": entity.id, "content": entity.content, "metadata": entity.metadata}
        for entity in EvolveClient().get_all_entities(CANDIDATES, filters={"type": "guideline"}, limit=1000)
    ]
    services.write_json(services.RESULTS / "invoice-candidates.json", rows)
    for row in rows:
        print(f"\nCandidate {row['id']}\n{row['content']}\nTraining session: {row['metadata'].get('session_id')}", flush=True)
    print("\nFull provenance saved in invoice-candidates.json.", flush=True)
    return rows


def learn(json_output=False):
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    state = services.load_state()
    training = state["sessions"].get("training", {})
    if not training.get("captured") or state.get("learned"):
        raise RuntimeError("Capture a completed, unlearned training session first.")
    if json_output:
        # Existing generation fallback for the gateway's unsupported strict schema.
        # The model, prompts, evidence and local Pydantic validation stay intact.
        from altk_evolve.llm.guidelines import consistency_guidelines

        consistency_guidelines.supports_response_schema = lambda **kwargs: False
    trajectories = json.loads((services.RESULTS / "training-trajectory.json").read_text())["trajectories"]
    assert all(trajectory["batch"]["conversation_id"] == training["id"] for trajectory in trajectories)
    client = EvolveClient()
    client.ensure_namespace(CANDIDATES)
    print("Learning only from training UUID", training["id"], flush=True)
    updates = [client.process_trajectory(trajectory, namespace_id=CANDIDATES).model_dump(mode="json") for trajectory in trajectories]
    manifest = {
        "training_session": training["id"],
        "results": updates,
        "model": state["learning_model"],
        "api_format": services.environment()["EVOLVE_CUSTOM_LLM_PROVIDER"],
        "output_mode": "existing JSON generation with local Pydantic validation" if json_output else "native response schema",
    }
    services.write_json(services.RESULTS / "learning.json", manifest)
    state.update(learned=True, learning_output_mode=manifest["output_mode"])
    services.save_state(state)
    candidates()


def promote(ids):
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    state = services.load_state()
    if not state.get("learned") or state.get("promoted"):
        raise RuntimeError("Promotion requires learned candidates and an unfrozen selection.")
    rows = {str(row["id"]): row for row in candidates()}
    ids = list(dict.fromkeys(ids or input("Reviewed candidate IDs (space-separated): ").split()))
    if not ids or any(identifier not in rows for identifier in ids):
        raise RuntimeError("Choose valid generated IDs. Nothing was promoted.")
    client = EvolveClient()
    client.ensure_namespace(REVIEWED)
    selected = []
    for identifier in ids:
        row = rows[identifier]
        metadata = {**row["metadata"], "status": "active", "candidate_id": identifier, "reviewed_at": datetime.now(UTC).isoformat()}
        updates = client.update_entities(
            REVIEWED, [Entity(type="guideline", content=row["content"], metadata=metadata)], enable_conflict_resolution=False
        )
        selected.extend(
            {"candidate_id": identifier, "id": update.id, "content": row["content"], "metadata": metadata} for update in updates
        )
    state["promoted"] = selected
    services.save_state(state)
    services.write_json(services.RESULTS / "invoice-promoted.json", selected)
    print(
        "Frozen reviewed guidelines:\n"
        + json.dumps([{key: row[key] for key in ("candidate_id", "id", "content")} for row in selected], indent=2)
    )
    print("Full provenance and candidate-to-serving mapping saved in invoice-promoted.json.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["check", "training", "baseline", "guided", "resume", "capture", "learn", "candidates", "promote", "status"]
    )
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--json-output", action="store_true", help="Use Evolve's JSON fallback for gateways without native schema support.")
    parser.add_argument(
        "--batch", action="store_true", help="Run without stdin using explicitly allowed local tools; save Claude's JSON result."
    )
    parser.add_argument("--repeat", type=int, help="Fresh repetition of the same packet, starting at 2; preserve earlier evidence.")
    args = parser.parse_args()
    services.configure_process()
    if args.command == "check":
        check()
    elif args.command in ("training", "baseline", "guided"):
        task(args.command, int(args.arguments[0]) if args.arguments else None, batch=args.batch, repeat=args.repeat)
    elif args.command == "resume":
        if not args.arguments or args.arguments[0] not in ("training", "baseline", "guided"):
            parser.error("resume requires training, baseline NUMBER, or guided NUMBER")
        arm = args.arguments[0]
        task(arm, int(args.arguments[1]) if len(args.arguments) > 1 else None, resume=True, batch=args.batch, repeat=args.repeat)
    elif args.command == "capture":
        capture(args.arguments[0])
    elif args.command == "learn":
        learn(args.json_output)
    elif args.command == "candidates":
        candidates()
    elif args.command == "promote":
        promote(args.arguments)
    else:
        print(json.dumps(services.load_state(), indent=2))


if __name__ == "__main__":
    main()
