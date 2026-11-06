"""Repository-quirk sessions: one training session, learning without feedback, guided reruns.

The sample repository has conventions an agent cannot guess and will run into: generated
code that must be regenerated, a test runner that builds the database, and commit checks
that require a migration and a changelog entry. Evolve learns them from the training
session's own errors; nobody grades or corrects that session.
"""

import argparse
import asyncio
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
CANDIDATES = services.LEARN_NAMESPACE
REVIEWED = services.SERVE_NAMESPACE
TASKS = json.loads((ROOT / "tasks.json").read_text())
TIMEOUT_SECONDS = 900
MAX_STEPS = 150
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


def prompt(task):
    return TASKS["template"].format(**task)


def evaluation_task(task_id):
    return next(task for task in TASKS["evaluation"] if task["id"] == task_id)


def check():
    services.health()
    config = services.RUNTIME / "claude-config" / ("check-" + str(uuid.uuid4()))
    shared.prepare_config(config)
    env = shared.claude_environment(config)
    probe = subprocess.run(
        [*shared.claude_command(), "-p", "Reply exactly REPO_READY. Do not use tools."],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode or "REPO_READY" not in probe.stdout:
        raise RuntimeError("Claude's live API check failed. Correct the private .env before starting tasks.")
    shared.check_reflection(env)
    state = services.load_state()
    state["checked_at"] = datetime.now(UTC).isoformat()
    state["placement"] = services.PLACEMENT
    services.save_state(state)
    print(json.dumps({"claude": "live API OK", "evolve": "live API OK", "model": state["model"], "placement": services.PLACEMENT}))


def fresh_repo(target):
    """A clean clone of the sample repository with its commit hooks installed."""
    shutil.copytree(ROOT / "shopkit", target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "core.hooksPath", ".githooks"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "chore: shopkit 0.4.0"],
    ):
        subprocess.run(command, cwd=target, check=True, capture_output=True)


def capture(label):
    from altk_evolve.sync.cortex_sync import CortexCursorStore, CortexSessionClient, ingest_session

    state = services.load_state()
    session = state["sessions"][label]
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
        raise RuntimeError(f"No main-agent trajectory captured for {label}.")
    services.write_json(services.RESULTS / f"{label}-trajectory.json", asdict(converted))
    session.update(captured=True, first_request_evolve=first_request_attribution(evidence))
    services.save_state(state)


def launch(label, task, arm):
    services.health()
    state = services.load_state()
    if not state.get("checked_at"):
        raise RuntimeError("Run ./demo setup first.")
    if label in state["sessions"]:
        raise RuntimeError(f"{label} already exists; evidence is not overwritten.")
    services.set_injection(arm == "guided")
    session = str(uuid.uuid4())
    target = services.RESULTS / "tasks" / label
    config = services.RUNTIME / "claude-config" / label
    fresh_repo(target)
    shared.prepare_config(config)
    env = shared.claude_environment(config)
    # The agent uses the repository's toolchain (system python3), not this driver's uv venv.
    env["PATH"] = ":".join(part for part in env["PATH"].split(":") if not part.startswith(str(ROOT / ".venv")))
    env.pop("VIRTUAL_ENV", None)
    state["sessions"][label] = {
        "id": session,
        "arm": arm,
        "task": task,
        "prompt": prompt(task),
        "placement": services.PLACEMENT,
        "started_at": datetime.now(UTC).isoformat(),
    }
    services.save_state(state)
    command = [*shared.claude_command(session), *BATCH_FLAGS, prompt(task)]
    print(f"{label}: {arm} session {session}", flush=True)
    started = time.monotonic()
    out, err = services.RESULTS / f"{label}-cli.json", services.RESULTS / f"{label}-cli.stderr.txt"
    with out.open("w") as stdout, err.open("w") as stderr:
        try:
            code = subprocess.run(
                command,
                cwd=target,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                timeout=TIMEOUT_SECONDS,
            ).returncode
        except subprocess.TimeoutExpired:
            code = 124
    state = services.load_state()
    state["sessions"][label].update(exit_code=code, elapsed_seconds=round(time.monotonic() - started, 1))
    services.save_state(state)
    capture(label)


def training():
    launch("training", TASKS["training"], "training")


def learn(json_output=True):
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    state = services.load_state()
    session = state["sessions"].get("training", {})
    if not session.get("captured") or state.get("learned"):
        raise RuntimeError("Capture a completed, unlearned training session first.")
    if state.get("promoted"):
        raise RuntimeError("The reviewed selection is frozen.")
    if json_output:
        # The gateway rejects strict response schemas; generation falls back to validated JSON.
        from altk_evolve.llm.guidelines import consistency_guidelines, guidelines

        consistency_guidelines.supports_response_schema = lambda **kwargs: False
        guidelines.supports_response_schema = lambda **kwargs: False
    trajectories = json.loads((services.RESULTS / "training-trajectory.json").read_text())["trajectories"]
    client = EvolveClient()
    client.ensure_namespace(CANDIDATES)
    # Evolve renders at most 50 steps of a Cortex session (the first 40 and last 10). A coding
    # session runs longer, and its commit rejections fall in the dropped middle; render it all.
    config = {"guidelines_mode": "standard", "trajectory_max_steps": MAX_STEPS}
    plan = client.processing.validate({"processors": [{"id": "guidelines", "plugin": "evolve.guidelines", "config": config}]})
    # No outcome and no feedback: Evolve sees only what happened in the session.
    updates = [client.process_trajectory(t, namespace_id=CANDIDATES, plan=plan).model_dump(mode="json") for t in trajectories]
    services.write_json(
        services.RESULTS / "learning.json", {"training_session": session["id"], "results": updates, "plan": plan.manifest()}
    )
    state["learned"] = True
    services.save_state(state)
    candidates()


def candidates():
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    rows = [
        {"id": e.id, "content": e.content, "metadata": e.metadata}
        for e in EvolveClient().get_all_entities(CANDIDATES, filters={"type": "guideline"}, limit=1000)
    ]
    services.write_json(services.RESULTS / "candidates.json", rows)
    for row in rows:
        print(f"\nCandidate {row['id']}\n{row['content']}", flush=True)
    return rows


def promote(ids):
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    state = services.load_state()
    if not state.get("learned") or state.get("promoted"):
        raise RuntimeError("Promotion requires learned candidates and an unfrozen selection.")
    rows = {str(row["id"]): row for row in candidates()}
    ids = list(dict.fromkeys(ids)) if ids else list(rows)
    if not ids or any(i not in rows for i in ids):
        raise RuntimeError("Choose valid candidate IDs. Nothing was promoted.")
    client = EvolveClient()
    client.ensure_namespace(REVIEWED)
    promoted = []
    for identifier in ids:
        row = rows[identifier]
        metadata = {**row["metadata"], "status": "active", "candidate_id": identifier, "reviewed_at": datetime.now(UTC).isoformat()}
        for update in client.update_entities(
            REVIEWED, [Entity(type="guideline", content=row["content"], metadata=metadata)], enable_conflict_resolution=False
        ):
            promoted.append({"candidate_id": identifier, "id": update.id, "content": row["content"]})
    state["promoted"] = promoted
    services.save_state(state)
    services.write_json(services.RESULTS / "promoted.json", promoted)
    print(json.dumps(promoted, indent=2))


async def retrieval_preview():
    from fastmcp import Client

    task = TASKS["evaluation"][0]
    async with Client(services.MCP) as client:
        result = await client.call_tool("get_guidelines_with_attribution", {"task": prompt(task), "namespace_id": REVIEWED}, timeout=90)
    payload = json.loads("\n".join(block.text for block in result.content if hasattr(block, "text")))
    if not payload.get("entity_ids"):
        raise RuntimeError("Promoted guidelines are not retrievable for the evaluation tasks.")
    return payload


def pairs(count):
    """Interleave baseline and guided sessions, rotating tasks and which arm goes first."""
    state = services.load_state()
    if not state.get("promoted"):
        raise RuntimeError("Promote guidelines first.")
    services.write_json(services.RESULTS / "retrieval-preview.json", asyncio.run(retrieval_preview()))
    sessions = state["sessions"]
    # A pair is complete when both arms finished and were captured. An interrupted pair is
    # finished first; a session that started but was never captured is set aside, not reused.
    complete = 0
    while all(sessions.get(f"{arm}-{complete + 1}", {}).get("captured") for arm in ("baseline", "guided")):
        complete += 1
    for index in range(complete, complete + count):
        task = TASKS["evaluation"][index % len(TASKS["evaluation"])]
        order = ("baseline", "guided") if index % 2 == 0 else ("guided", "baseline")
        for arm in order:
            label = f"{arm}-{index + 1}"
            existing = services.load_state()["sessions"].get(label)
            if existing and existing.get("captured"):
                continue
            if existing:
                set_aside(label)
            launch(label, task, arm)
    services.set_injection(False)


def set_aside(label):
    """Keep an interrupted session's evidence under a new name so its label can be rerun."""
    state = services.load_state()
    suffix = 1
    while f"{label}-interrupted-{suffix}" in state["sessions"]:
        suffix += 1
    aside = f"{label}-interrupted-{suffix}"
    state["sessions"][aside] = {**state["sessions"].pop(label), "arm": "interrupted"}
    services.save_state(state)
    for path in services.RESULTS.glob(f"{label}-*"):
        if path.name.startswith(f"{label}-interrupted-"):
            continue
        path.rename(path.with_name(path.name.replace(label, aside, 1)))
    task_dir = services.RESULTS / "tasks" / label
    if task_dir.exists():
        task_dir.rename(services.RESULTS / "tasks" / aside)
    print(f"{label}: an earlier attempt was interrupted; kept as {aside}", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("check", "training", "learn", "candidates", "promote", "pairs", "status"))
    parser.add_argument("arguments", nargs="*")
    parser.add_argument("--count", type=int, default=5, help="Baseline/guided pairs to add.")
    args = parser.parse_args()
    services.configure_process()
    if args.command == "check":
        check()
    elif args.command == "training":
        training()
    elif args.command == "learn":
        learn()
    elif args.command == "candidates":
        candidates()
    elif args.command == "promote":
        promote(args.arguments)
    elif args.command == "pairs":
        pairs(args.count)
    else:
        print(json.dumps(services.load_state(), indent=2))


if __name__ == "__main__":
    main()
