"""Interactive photo tasks and an explicit capture, learn, review, promote loop."""

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

import httpx

from services import (
    API,
    BRIDGE,
    CA,
    MCP,
    RESULTS,
    ROOT,
    RUNTIME,
    configure_process,
    environment,
    health,
    load_state,
    save_state,
    set_injection,
    write_json,
)

PHOTO_SHA256 = "15f46ed3db89d27635c4a1f1c85616bdcac2ae96d3245e53426ee1e7c55d6353"
ARMS = ("baseline", "training", "guided")


def prompt(arm):
    return (ROOT / "prompts" / ("gps.txt" if arm == "training" else "focal.txt")).read_text().strip()


def claude_environment(config):
    env = environment()
    env.update(
        HTTP_PROXY="http://127.0.0.1:47800",
        HTTPS_PROXY="http://127.0.0.1:47800",
        http_proxy="http://127.0.0.1:47800",
        https_proxy="http://127.0.0.1:47800",
        ALL_PROXY="",
        all_proxy="",
        NO_PROXY="",
        no_proxy="",
        NODE_EXTRA_CA_CERTS=str(CA / "ca.crt"),
        ANTHROPIC_BASE_URL=BRIDGE,
        CLAUDE_CONFIG_DIR=str(config),
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1",
        DISABLE_AUTOUPDATER="1",
    )
    return env


def prepare_config(config):
    config.mkdir(parents=True, exist_ok=False)
    # The reader chose this key in .env. Accept it only in this fresh demo config.
    # Claude stores a key suffix in its config; this file stays private in runtime/.
    write_json(
        config / ".claude.json",
        {
            "hasCompletedOnboarding": True,
            "theme": "dark",
            "customApiKeyResponses": {"approved": [environment()["ANTHROPIC_API_KEY"][-20:]], "rejected": []},
        },
    )
    (config / ".claude.json").chmod(0o600)


def claude_command(session=None):
    command = [
        "claude",
        "--bare",
        "--model",
        load_state()["model"],
        "--tools",
        "Bash,Read,Write,Edit",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--setting-sources",
        "",
    ]
    if session:
        command.extend(["--session-id", session])
    return command


def check():
    health()
    if hashlib.sha256((ROOT / "sample.jpg").read_bytes()).hexdigest() != PHOTO_SHA256:
        raise RuntimeError("The sample photo checksum does not match.")
    state = load_state()
    config = RUNTIME / "claude-config" / ("check-" + str(uuid.uuid4()))
    prepare_config(config)
    env = claude_environment(config)
    auth = subprocess.run(["claude", "auth", "status", "--json"], env=env, capture_output=True, text=True, timeout=20)
    try:
        authenticated = json.loads(auth.stdout).get("loggedIn") is True
    except ValueError:
        authenticated = False
    if not authenticated:
        raise RuntimeError("Claude does not recognize the mounted API key. Check .env and run ./demo setup again.")
    # A separate, tool-free diagnostic request is excluded from all task evidence.
    probe = subprocess.run(
        [*claude_command(), "-p", "Reply with exactly PHOTO_READY. Do not use tools."],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if probe.returncode or "PHOTO_READY" not in probe.stdout:
        raise RuntimeError(
            "Claude's live API check failed. Check the Anthropic endpoint, key, and model in .env. Task runs have not started."
        )
    from litellm import completion

    # Reflection uses a different API; validate it before anyone spends time on tasks.
    try:
        answer = completion(
            model=env["EVOLVE_MODEL_NAME"],
            custom_llm_provider=env["EVOLVE_CUSTOM_LLM_PROVIDER"],
            messages=[{"role": "user", "content": "Reply with exactly EVOLVE_READY."}],
            max_tokens=32,
            timeout=90,
        )
    except Exception as exc:
        raise RuntimeError("Evolve's live API check failed. Check its OpenAI-compatible URL, key, and model in .env.") from exc
    if "EVOLVE_READY" not in (answer.choices[0].message.content or ""):
        raise RuntimeError("Evolve's live API check failed. Check its model and OpenAI-compatible credentials.")
    state["checked_at"] = datetime.now(UTC).isoformat()
    save_state(state)
    print(
        json.dumps(
            {
                "photo_sha256": PHOTO_SHA256,
                "claude": "live API OK",
                "evolve": "live API OK",
                "task_model": state["model"],
                "reflection_model": state["learning_model"],
            },
            indent=2,
        )
    )


def capture(arm):
    from altk_evolve.sync.cortex_sync import CortexCursorStore, CortexSessionClient, ingest_session
    from report import first_request_attribution

    state = load_state()
    session = state["sessions"].get(arm)
    if not session:
        raise RuntimeError(f"No {arm} session has been launched.")
    with httpx.Client(trust_env=False, timeout=20) as client:
        response = client.get(API + f"/v1/sessions/{session['id']}", params={"limit": 2000})
        if response.status_code == 404:
            raise RuntimeError("Cortex captured no task traffic. Keep the failed run and check ./demo check.")
        response.raise_for_status()
        evidence = response.json()
    write_json(RESULTS / f"{arm}-cortex-session.json", evidence)
    cortex = CortexSessionClient(API)
    try:
        converted = ingest_session(
            None,
            cortex,
            session["id"],
            namespace_id="photo-candidates",
            cursors=CortexCursorStore(RUNTIME / "export-cursors.json"),
            min_new_turns=1,
            dry_run=True,
        )
    finally:
        cortex.close()
    if not converted.trajectories or not any(t["messages"] for t in converted.trajectories):
        raise RuntimeError("No main-agent trajectory was captured. Inspect the saved Cortex session.")
    write_json(RESULTS / f"{arm}-trajectory.json", asdict(converted))
    first = first_request_attribution(evidence)
    session["captured"] = True
    session["first_request_evolve"] = first
    save_state(state)
    calls = sum(len(m.get("tool_calls") or []) for t in converted.trajectories for m in t["messages"])
    print(
        json.dumps(
            {"arm": arm, "session": session["id"], "tool_calls": calls, "first_request_evolve": first, "saved": f"{arm}-trajectory.json"},
            indent=2,
        )
    )


def task(arm):
    health()
    state = load_state()
    if not state.get("checked_at"):
        raise RuntimeError("Run ./demo setup (including the live API check) first.")
    if arm in state["sessions"]:
        raise RuntimeError(
            f"This run already has a {arm} session. Use ./demo capture {arm} to recover evidence; use a fresh results directory to retry."
        )
    if arm == "baseline" and (state.get("learned") or state.get("promoted")):
        raise RuntimeError("Run baseline before learning or promotion.")
    if arm == "training" and not state["sessions"].get("baseline", {}).get("captured"):
        raise RuntimeError("Finish and capture baseline before training.")
    if arm == "guided" and not state.get("promoted"):
        raise RuntimeError("Review and promote generated guidelines before running guided.")
    set_injection(arm == "guided")
    if arm == "guided":
        asyncio.run(retrieve())
    session = str(uuid.uuid4())
    target = RESULTS / "photos" / arm
    target.mkdir(parents=True, exist_ok=False)
    shutil.copy2(ROOT / "sample.jpg", target / "sample.jpg")
    config = RUNTIME / "claude-config" / arm
    prepare_config(config)
    state["sessions"][arm] = {"id": session, "started_at": datetime.now(UTC).isoformat(), "prompt": prompt(arm)}
    save_state(state)
    print(
        f"Launching interactive Claude Code: {arm}, session {session}.\nThe task is already supplied. Approve local tool calls normally; enter /exit after the final answer.",
        flush=True,
    )
    if arm == "training":
        print("After the location answer, paste this follow-up in Claude:\n\n" + (ROOT / "prompts/verify-gps.txt").read_text(), flush=True)
    started = time.monotonic()
    try:
        completed = subprocess.run([*claude_command(session), prompt(arm)], cwd=target, env=claude_environment(config))
        code = completed.returncode
    except KeyboardInterrupt:
        code = 130
    state = load_state()
    state["sessions"][arm].update(exit_code=code, elapsed_seconds=round(time.monotonic() - started, 2))
    save_state(state)
    capture(arm)
    output = target / ("gps-result.json" if arm == "training" else "photo-result.json")
    if output.exists():
        try:
            document = json.loads(output.read_text())
            tags = document[0] if isinstance(document, list) and document else document
            keys = (
                ("GPSLatitude", "GPSLongitude")
                if arm == "training"
                else ("FocalLength", "FocalLengthIn35mmFormat", "FocalLengthIn35mmFilm")
            )
            print("Saved metadata (task tags):\n" + json.dumps({k: tags[k] for k in keys if k in tags}, indent=2))
        except (ValueError, TypeError, KeyError):
            print(f"Inspect {output.name}; it does not contain the expected metadata object.")
        print(f"Full output: photos/{arm}/{output.name}")
    else:
        print(f"No {output.name} was saved. This remains a recorded task failure.")


def candidates():
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    rows = [
        {"id": e.id, "content": e.content, "metadata": e.metadata}
        for e in EvolveClient().get_all_entities("photo-candidates", filters={"type": "guideline"}, limit=1000)
    ]
    write_json(RESULTS / "photo-candidates.json", rows)
    for row in rows:
        metadata = row["metadata"]
        print(f"\nCandidate {row['id']}\n{row['content']}")
        provenance = {
            k: metadata[k]
            for k in (
                "session_id",
                "source_task_id",
                "task_description",
                "sources",
                "rationale",
                "category",
                "trigger",
                "generation_method",
            )
            if k in metadata
        }
        print(json.dumps(provenance, indent=2))
    print("Full guideline metadata: photo-candidates.json")
    if not rows:
        print("No guidelines were generated. Keep this result; there is nothing to promote.")
    return rows


def learn():
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    state = load_state()
    session = state["sessions"].get("training", {})
    if not session.get("captured"):
        raise RuntimeError("Finish and capture the GPS training session first.")
    if state.get("learned"):
        print("The selected training session was already processed.")
        candidates()
        return
    trajectories = json.loads((RESULTS / "training-trajectory.json").read_text())["trajectories"]
    if any(t["batch"]["conversation_id"] != session["id"] for t in trajectories):
        raise RuntimeError("The saved trajectory does not match the selected training session.")
    client = EvolveClient()
    client.ensure_namespace("photo-candidates")
    print(f"Learning only from GPS session {session['id']}; baseline and guided captures are excluded.", flush=True)
    updates = [client.process_trajectory(t, namespace_id="photo-candidates").model_dump(mode="json") for t in trajectories]
    write_json(RESULTS / "learning.json", {"training_session": session["id"], "results": updates})
    state["learned"] = True
    save_state(state)
    candidates()


def promote(ids):
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.schema.core import Entity

    state = load_state()
    if not state.get("learned"):
        raise RuntimeError("Run ./demo learn first.")
    if state.get("promoted"):
        raise RuntimeError("This comparison's reviewed selection is frozen. Inspect photo-promoted.json.")
    rows = candidates()
    if not rows:
        return
    if not ids:
        print(
            "Choose reusable procedure advice after reading the full text and provenance. Exclude fixture-specific answers and invented failures."
        )
        ids = input("Reviewed candidate IDs to promote (space-separated): ").split()
    ids = list(dict.fromkeys(ids))
    if not ids:
        raise RuntimeError("No selection was promoted.")
    by_id = {str(row["id"]): row for row in rows}
    if any(identifier not in by_id for identifier in ids):
        raise RuntimeError("An ID is not a generated guideline in photo-candidates. Nothing was copied.")
    client = EvolveClient()
    client.ensure_namespace("photo-reviewed")
    promoted = []
    for identifier in ids:
        row = by_id[identifier]
        metadata = {**row["metadata"], "status": "active", "candidate_id": identifier, "reviewed_at": datetime.now(UTC).isoformat()}
        updates = client.update_entities(
            "photo-reviewed", [Entity(type="guideline", content=row["content"], metadata=metadata)], enable_conflict_resolution=False
        )
        promoted.extend({"candidate_id": identifier, "id": u.id, "content": row["content"], "metadata": metadata} for u in updates)
    state["promoted"] = promoted
    save_state(state)
    write_json(RESULTS / "photo-promoted.json", promoted)
    print(json.dumps([{k: row[k] for k in ("candidate_id", "id", "content")} for row in promoted], indent=2))
    print("Reviewed copies are now in photo-reviewed. Candidate text and provenance were preserved.")


async def retrieve():
    from fastmcp import Client

    async with Client(MCP) as client:
        result = await client.call_tool(
            "get_guidelines_with_attribution", {"task": prompt("guided"), "namespace_id": "photo-reviewed"}, timeout=90
        )
    payload = json.loads("\n".join(block.text for block in result.content if hasattr(block, "text")))
    write_json(RESULTS / "photo-retrieval.json", payload)
    if not payload.get("entity_ids") or not payload.get("text"):
        raise RuntimeError("Evolve retrieved no reviewed guidelines. Inspect photo-retrieval.json before running guided.")
    print("Previewed reviewed guideline IDs:", payload["entity_ids"], flush=True)


def compare():
    from report import build_report

    state = load_state()
    if any(not state["sessions"].get(arm, {}).get("captured") for arm in ("baseline", "guided")):
        raise RuntimeError("Capture both baseline and guided before comparing.")
    metrics = build_report(RESULTS, state)
    print(json.dumps(metrics, indent=2))
    print("Open results/comparison.html on your host to read the actual outputs, guidelines, attribution, and trajectories.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=[*ARMS, "check", "capture", "learn", "candidates", "promote", "compare", "status"])
    parser.add_argument("arguments", nargs="*")
    args = parser.parse_args()
    try:
        configure_process()
        if args.command == "promote":
            promote(args.arguments)
        elif args.command == "capture":
            if len(args.arguments) != 1 or args.arguments[0] not in ARMS:
                parser.error("capture requires baseline, training, or guided")
            capture(args.arguments[0])
        elif args.arguments:
            parser.error("Unexpected arguments")
        elif args.command in ARMS:
            task(args.command)
        elif args.command == "status":
            print(json.dumps(load_state(), indent=2))
        else:
            {"check": check, "learn": learn, "candidates": candidates, "compare": compare}[args.command]()
    except RuntimeError as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()
