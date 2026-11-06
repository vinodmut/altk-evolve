"""Run a bounded, real Claude Code / Cortex / Evolve comparison on synthetic notes.

Credentials are read at runtime from the existing Claude sandbox env file and
passed only through subprocess environments. No credentials are written here.
Each trial has a fresh working directory, Claude config directory and session.
The only steering added to the evaluation arm is the reviewed Evolve guideline.
"""

import argparse
import asyncio
import json
import os
import re
import random
import shutil
import signal
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
ARTIFACTS = HERE / "artifacts"
STATE = ARTIFACTS / "state.json"
# Override with --credentials / --ca-dir, or the CORTEX_DEMO_CREDENTIALS / CORTEX_DEMO_CA_DIR environment.
CREDENTIALS = Path(os.environ.get("CORTEX_DEMO_CREDENTIALS", Path.home() / "creds" / "claude-sandbox.env"))
CORTEX = REPO.parent / "cortex" / "bin" / "cortex"
CA_DIR = Path(os.environ.get("CORTEX_DEMO_CA_DIR", Path.home() / ".cortex" / "ca"))
PROXY = "http://127.0.0.1:47800"
SESSION_API = "http://127.0.0.1:47801"
MCP = "http://127.0.0.1:18201/sse"
LEARN_NS = "bucket-list-candidates"
SERVE_NS = "bucket-list-reviewed"
TASK_TOOL = "notes.py"
PROFILE = "bucket-list-demo"
REVIEW_PURPOSE = "reusable note-selection and checkbox-counting procedure"
INFERENCE_BASE_URL = None
PROMPT = (
    "How many activities are done in my current bucket list note? "
    "Use the local notes service via `python3 notes.py` (its --help describes the API). "
    "Return the result as JSON with note_id and completed."
)
SOURCE = "https://huggingface.co/blog/ibm-research/altk-evolve-consistency"
EVALUATION_ARMS = ("baseline", "evolve", "evolve-consistency")
CASES = {
    "train": {
        "active_id": "note-204",
        "activities": [True, False, True, True, False, False],
        "legend": "Legend: [x] means done; [ ] means planned.",
        "title": "My bucket list — [x] / [ ]",
    },
    "a": {
        "active_id": "note-517",
        "activities": [True, False, True, True, False, True, True, False],
        "legend": "Status key: [x] done, [ ] still on the list.",
        "title": "My bucket list — [x] done",
    },
    "b": {
        "active_id": "note-831",
        "activities": [False, True, False, True, False, True, False],
        "legend": "Use [x] for finished activities and [ ] for future ones.",
        "title": "My bucket list",
    },
    "c": {
        "active_id": "note-692",
        "activities": [True, True, False, True, True, False, True, True, False, False],
        "legend": "Legend: completed = [x]; pending = [ ].",
        "title": "My bucket list — progress [x]",
    },
}

# A second, explicitly recorded screening round uses longer lists. The small
# cases above were all solved correctly and produced no useful guideline.
for case_name, seed, count, note_id in [
    ("long_train", 31, 47, "note-938"),
    ("long_a", 53, 43, "note-173"),
    ("long_b", 79, 59, "note-465"),
    ("long_c", 97, 51, "note-726"),
]:
    rng = random.Random(seed)
    CASES[case_name] = {
        "active_id": note_id,
        "activities": [rng.random() < 0.57 for _ in range(count)],
        "legend": "Legend: [x] means completed; [ ] means pending. The symbol [x] here explains the notation.",
        "title": "My bucket list — [x] / [ ]",
    }


def read_state():
    return json.loads(STATE.read_text())


def write_state(state):
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2) + "\n")


def base_env(model=None):
    values = {k: v for k, v in dotenv_values(CREDENTIALS).items() if v}
    token = values.get("ANTHROPIC_AUTH_TOKEN") or values.get("ANTHROPIC_API_KEY")
    if not token:
        raise RuntimeError("The configured sandbox credential file has no API credential")
    base = values["ANTHROPIC_BASE_URL"].rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    env = os.environ.copy()
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        env.pop(name, None)
    env.update(values)
    env.pop("ANTHROPIC_AUTH_TOKEN", None)
    env.update(
        ANTHROPIC_API_KEY=token,
        ANTHROPIC_BASE_URL=base,
        CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1",
        CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
        CLAUDE_CODE_DISABLE_AUTO_MEMORY="1",
        OPENAI_API_KEY=token,
        OPENAI_BASE_URL=base + "/v1",
        EVOLVE_CUSTOM_LLM_PROVIDER="openai",
        EVOLVE_MODEL_NAME=model or "Azure/gpt-4.1",
        EVOLVE_BACKEND="filesystem",
        EVOLVE_DATA_DIR=str(ARTIFACTS / "runtime" / "data"),
        EVOLVE_SQLITE_PATH=str(ARTIFACTS / "runtime" / "entities.sqlite.db"),
        EVOLVE_NAMESPACE_ID=SERVE_NS,
        EVOLVE_GUIDELINES_MODE="standard",
        EVOLVE_INJECTION_MODE="retrieval",
        EVOLVE_OTEL_ENABLED="false",
        EVOLVE_RETENTION_SCHEDULER_ENABLED="false",
        EVOLVE_HOOKS_CONFIG="",
    )
    return env


def preflight():
    if STATE.exists():
        raise RuntimeError("An experiment already exists; inspect artifacts/state.json before replacing anything")
    if not CORTEX.is_file() or not (CA_DIR / "ca.crt").is_file():
        raise RuntimeError("The existing development Cortex binary and trusted CA are required")
    env = base_env()
    catalog = None
    with httpx.Client(trust_env=False, timeout=25) as client:
        for path in ("/v1/models", "/models"):
            response = client.get(env["ANTHROPIC_BASE_URL"] + path, headers={"Authorization": "Bearer " + env["OPENAI_API_KEY"]})
            if response.status_code == 200:
                catalog = [m["id"] for m in response.json().get("data", [])]
                break
    if not catalog:
        raise RuntimeError("Could not read the configured gateway model catalog")
    preferred = ["Azure/gpt-4.1", "gpt-4.1", "claude-sonnet-4-5"]
    model = next((m for m in preferred if m in catalog), None)
    if model is None:
        model = next((m for m in catalog if "gpt-4.1" in m.lower() and "mini" not in m.lower()), None)
    if model is None:
        raise RuntimeError("Neither the paper's GPT-4.1 nor the configured Claude model was found in the gateway catalog")
    state = {"created_at": datetime.now(UTC).isoformat(), "model": model, "source": SOURCE, "trials": [], "services": {}}
    write_state(state)
    print(
        json.dumps(
            {
                "model": model,
                "source": SOURCE,
                "task": PROMPT,
                "held_out_cases": [case for case in CASES if case not in ("train", "long_train")],
            },
            indent=2,
        )
    )


def config(learn=False):
    return {
        "mode": "proxy-sidecar",
        "listener": {
            "roles": ["forward"],
            "bind_loopback_only": True,
            "forward_proxy_addr": "127.0.0.1:47800",
            "session_api_addr": "127.0.0.1:47801",
            "transparent_proxy_addr": "127.0.0.1:47803",
            "health_addr": "127.0.0.1:47804",
        },
        "stats": {"address": "127.0.0.1:47802"},
        "tls_bridge": {"mode": "enabled", "ca_dir": str(CA_DIR), "generate_ca": False},
        "cost_ledger": {"enabled": False},
        "pipeline": {
            "outbound": {
                "plugins": [
                    {"name": "inference-parser"},
                    {"name": "mcp-parser"},
                    {"name": "a2a-parser"},
                    {
                        "name": "evolve",
                        "config": {
                            "endpoint": MCP,
                            "agents": ["claude-code"],
                            "serve_namespace": SERVE_NS,
                            "learn_namespace": LEARN_NS,
                            "processing_profile": PROFILE,
                            "learn": learn,
                            "inject": True,
                            "idle_after_seconds": 60,
                            "cache_ttl_seconds": 30,
                            "timeout_ms": 15000,
                            "learn_timeout_seconds": 180,
                        },
                    },
                ]
            }
        },
    }


def set_learning(learn):
    path = ARTIFACTS / "runtime" / "cortex.yaml"
    path.write_text(yaml.safe_dump(config(learn), sort_keys=False))
    time.sleep(3)


def start():
    state = read_state()
    if state["services"]:
        raise RuntimeError("Services have already been started")
    runtime = ARTIFACTS / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    env = base_env(state["model"])
    configs = runtime / "cortex.yaml"
    configs.write_text(yaml.safe_dump(config(), sort_keys=False))
    processes = {}
    commands = {
        "evolve": ["uv", "run", "--no-sync", "evolve-mcp", "--transport", "sse", "--port", "18201"],
        "cortex": [str(CORTEX), "--config", str(configs)],
    }
    for name, cmd in commands.items():
        with (runtime / f"{name}.log").open("w") as log:
            process = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=log, start_new_session=True)
        processes[name] = process
        state["services"][name] = {"pid": process.pid, "command": cmd}
    write_state(state)
    with httpx.Client(timeout=2, trust_env=False) as client:
        for _ in range(60):
            if any(p.poll() is not None for p in processes.values()):
                raise RuntimeError("A service exited; inspect artifacts/runtime/*.log")
            try:
                response = client.get(SESSION_API + "/v1/pipeline")
                client.get(MCP, timeout=0.5)
            except httpx.ReadTimeout:
                if response.status_code == 200:
                    names = [p["name"] for p in response.json()["outbound"]]
                    if "evolve" in names:
                        print(json.dumps({"started": list(processes), "pipeline": names}))
                        return
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise RuntimeError("Services did not become ready in 60 seconds")


def prepare_trial(label, case):
    target = ARTIFACTS / "trials" / label
    if target.exists():
        raise RuntimeError(f"Trial {label} already exists")
    target.mkdir(parents=True)
    shutil.copy2(HERE / "notes.py", target / "notes.py")
    fixture = CASES[case]
    items = [f"- [{'x' if done else ' '}] Activity {i + 1}" for i, done in enumerate(fixture["activities"])]
    notes = [
        {
            "id": "note-archive",
            "title": "My bucket list — old",
            "archived": True,
            "content": "\n".join(["Old bucket list"] + [f"- [x] Archived activity {i}" for i in range(8)]),
        },
        {
            "id": fixture["active_id"],
            "title": fixture["title"],
            "archived": False,
            "content": "\n".join([fixture["title"], fixture["legend"], "", *items]),
        },
        {"id": "note-shopping", "title": "Shopping list", "archived": False, "content": "- [x] Bread\n- [ ] Milk"},
    ]
    (target / "notes.json").write_text(json.dumps(notes, indent=2) + "\n")
    settings = {"env": {"HTTPS_PROXY": PROXY, "NODE_EXTRA_CA_CERTS": str(CA_DIR / "ca.crt")}}
    (target / "settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    return target


def decode_answer(text):
    candidates = [text.strip(), *re.findall(r"\{[^{}]*\}", text)]
    for candidate in candidates:
        try:
            result = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(result, dict) and "note_id" in result and "completed" in result:
            return result
    return None


def grade(case, answer):
    fixture = CASES[case]
    expected = {"note_id": fixture["active_id"], "completed": sum(fixture["activities"])}
    valid = isinstance(answer, dict) and type(answer.get("completed")) is int
    return {"expected": expected, "passed": valid and answer == expected}


def session_evidence(session_id):
    with httpx.Client(timeout=15, trust_env=False) as client:
        response = client.get(SESSION_API + f"/v1/sessions/{session_id}")
        response.raise_for_status()
    events = response.json().get("events", [])
    return [
        {
            "seq": e["seq"],
            "phase": e.get("phase"),
            "evolve": (e.get("plugins") or {}).get("evolve"),
            "invocations": [i for i in (e.get("invocations") or {}).get("outbound", []) if i.get("plugin") == "evolve"],
        }
        for e in events
        if e.get("phase") == "request" and e.get("inference")
    ]


def failed_commands(records):
    calls = {
        b["id"]: b.get("input", {}).get("command", "")
        for r in records
        if r.get("type") == "assistant"
        for b in r.get("message", {}).get("content", [])
        if b.get("type") == "tool_use" and b.get("name") == "Bash"
    }
    return [
        calls[b["tool_use_id"]]
        for r in records
        if r.get("type") == "user"
        for b in r.get("message", {}).get("content", [])
        if b.get("type") == "tool_result" and b.get("is_error") and b.get("tool_use_id") in calls
    ]


def run_trial(label, case, arm, *, resume=None, prompt=PROMPT, suffix=None):
    state = read_state()
    if arm in EVALUATION_ARMS:
        plugin = next(
            p
            for p in yaml.safe_load((ARTIFACTS / "runtime" / "cortex.yaml").read_text())["pipeline"]["outbound"]["plugins"]
            if p["name"] == "evolve"
        )
        if plugin["config"]["learn"]:
            raise RuntimeError("Disable learning before evaluation so held-out sessions cannot enter training")
        if arm == "baseline" and state.get("promoted") and plugin["config"]["inject"]:
            raise RuntimeError("Disable injection or run baselines before promoting guidelines")
        if arm != "baseline" and not state.get("promoted"):
            raise RuntimeError("Review and promote learned guidelines before running the Evolve arm")
        if arm != "baseline" and not plugin["config"]["inject"]:
            raise RuntimeError("Enable injection before running the Evolve arm")
    target = ARTIFACTS / "trials" / label if resume else prepare_trial(label, case)
    suffix = suffix or ("feedback" if resume else "initial")
    if (target / f"{suffix}.metrics.json").exists():
        raise RuntimeError(f"Trial output {label}/{suffix} already exists")
    env = base_env(state["model"])
    env.update(CLAUDE_CONFIG_DIR=str(target / "claude-config"), HTTPS_PROXY=PROXY, NODE_EXTRA_CA_CERTS=str(CA_DIR / "ca.crt"))
    if INFERENCE_BASE_URL:
        env.update(ANTHROPIC_BASE_URL=INFERENCE_BASE_URL, HTTP_PROXY=PROXY)
        env.pop("NO_PROXY", None)
        env.pop("no_proxy", None)
    session_id = resume or str(uuid.uuid4())
    cmd = [
        "claude",
        "--bare",
        "--disable-slash-commands",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
        "--settings",
        str(target / "settings.json"),
        "--model",
        state["model"],
        "--tools",
        "Bash",
        "--allowedTools",
        f"Bash(python3 {TASK_TOOL}:*)",
        "Bash(python3 -c:*)",
        "--max-budget-usd",
        "1.00",
        "--output-format",
        "stream-json",
        "--verbose",
    ]
    cmd += ["--resume", resume] if resume else ["--session-id", session_id]
    cmd += ["-p", prompt]
    started = time.monotonic()
    timed_out = False
    try:
        result = subprocess.run(cmd, cwd=target, env=env, capture_output=True, text=True, timeout=240)
    except subprocess.TimeoutExpired as error:
        timed_out = True
        stdout = error.stdout or ""
        stderr = error.stderr or ""
        result = subprocess.CompletedProcess(
            cmd,
            124,
            stdout=stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout,
            stderr=stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr,
        )
    (target / f"{suffix}.jsonl").write_text(result.stdout)
    (target / f"{suffix}.stderr.log").write_text(result.stderr)
    records = []
    for line in result.stdout.splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            pass
    final = next((r for r in reversed(records) if r.get("type") == "result"), {})
    if timed_out:
        final = {
            "subtype": "timeout",
            "result": "",
            "num_turns": sum(r.get("type") == "assistant" for r in records),
        }
    answer = decode_answer(final.get("result", ""))
    assessment = grade(case, answer)
    if timed_out:
        assessment["passed"] = False
        assessment["checks"] = {**assessment.get("checks", {}), "completed_within_time_limit": False}
    commands = [
        b.get("input", {}).get("command", "")
        for r in records
        if r.get("type") == "assistant"
        for b in r.get("message", {}).get("content", [])
        if b.get("type") == "tool_use" and b.get("name") == "Bash"
    ]
    evidence = session_evidence(session_id)
    metrics = {
        "label": label,
        "case": case,
        "arm": arm,
        "feedback": bool(resume),
        "phase": suffix,
        "session_id": session_id,
        "exit_code": result.returncode,
        "timed_out": timed_out,
        "turn_count_source": "partial_transcript" if timed_out else "claude_result",
        "subtype": final.get("subtype"),
        "answer": answer,
        **assessment,
        "duration_seconds": round(time.monotonic() - started, 2),
        "cost_usd": final.get("total_cost_usd"),
        "usage": final.get("usage"),
        "model_usage": final.get("modelUsage"),
        "bash_commands": commands,
        "failed_bash_commands": failed_commands(records),
        "num_turns": final.get("num_turns"),
        "evidence": evidence,
        "injected_ids": sorted({gid for e in evidence for gid in (e.get("evolve") or {}).get("ids", [])}),
    }
    (target / f"{suffix}.metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    state["trials"].append(metrics)
    write_state(state)
    print(
        json.dumps(
            {k: metrics[k] for k in ("label", "case", "arm", "passed", "answer", "expected", "num_turns", "cost_usd", "injected_ids")},
            indent=2,
        )
    )
    if (result.returncode or not final) and not timed_out:
        raise RuntimeError("Claude session failed; inspect trial stderr and initial.jsonl")
    return metrics


def client():
    state = read_state()
    os.environ.update(base_env(state["model"]))
    from altk_evolve.frontend.client.evolve_client import EvolveClient

    return EvolveClient()


def candidates():
    c = client()
    if not c.namespace_exists(LEARN_NS):
        print("No candidates yet")
        return
    entities = c.get_all_entities(LEARN_NS, filters={"type": "guideline"})
    data = [{"id": e.id, "content": e.content, "metadata": e.metadata} for e in entities]
    (ARTIFACTS / "candidates.json").write_text(json.dumps(data, indent=2) + "\n")
    print(json.dumps(data, indent=2))


def mine(label, mode):
    state = read_state()
    trial = next(t for t in reversed(state["trials"]) if t["label"] == label)
    env = base_env(state["model"])
    env["EVOLVE_GUIDELINES_MODE"] = mode
    os.environ.update(env)
    from altk_evolve.frontend.client.evolve_client import EvolveClient
    from altk_evolve.sync.cortex_sync import CortexCursorStore, CortexSessionClient, ingest_session

    c = EvolveClient()
    cortex = CortexSessionClient(SESSION_API)
    try:
        result = ingest_session(
            c,
            cortex,
            trial["session_id"],
            namespace_id=LEARN_NS,
            cursors=CortexCursorStore(ARTIFACTS / "runtime" / f"{mode}-cursors.json"),
        )
    finally:
        cortex.close()
    state["mining"] = {"training_label": label, "mode": mode, "method": "fast", "result": result.__dict__}
    state.setdefault("mining_history", []).append(state["mining"])
    (ARTIFACTS / f"mining-{label}-{mode}.json").write_text(json.dumps(state["mining"], indent=2) + "\n")
    write_state(state)
    print(json.dumps(state["mining"], indent=2))


def promote(ids):
    from altk_evolve.schema.core import Entity

    c = client()
    c.ensure_namespace(SERVE_NS)
    copied = []
    for identifier in ids:
        entity = c.get_entity_by_id(LEARN_NS, identifier)
        if entity is None or entity.type != "guideline":
            raise RuntimeError(f"Unknown candidate {identifier}")
        metadata = {**entity.metadata, "status": "active", "reviewed_for": REVIEW_PURPOSE}
        updates = c.update_entities(
            SERVE_NS, [Entity(type="guideline", content=entity.content, metadata=metadata)], enable_conflict_resolution=False
        )
        copied.extend({"id": u.id, "content": entity.content, "candidate_id": identifier} for u in updates)
    state = read_state()
    if state.get("promoted") and not state.get("promotion_history"):
        state["promotion_history"] = [{"at": None, "guidelines": state["promoted"]}]
    state["promoted"] = [*state.get("promoted", []), *copied]
    state.setdefault("promotion_history", []).append({"at": datetime.now(UTC).isoformat(), "guidelines": copied})
    write_state(state)
    print(json.dumps(copied, indent=2))


async def warm():
    from fastmcp import Client

    async with Client(MCP) as mcp:
        response = await mcp.call_tool(
            "get_guidelines_with_attribution",
            {"task": PROMPT, "namespace_id": SERVE_NS, "session_id": "demo-retrieval-warmup"},
            timeout=90,
        )
    blocks = [block.text for block in response.content if hasattr(block, "text")]
    (ARTIFACTS / "retrieval-warmup.json").write_text("\n".join(blocks) + "\n")
    print("\n".join(blocks))


def stop():
    state = read_state()
    for service in state["services"].values():
        try:
            os.killpg(service["pid"], signal.SIGTERM)
        except ProcessLookupError:
            pass
    state["services_stopped_at"] = datetime.now(UTC).isoformat()
    write_state(state)
    print("Stopped only the processes started by this experiment")


def summarize():
    state = read_state()
    arms = {}
    for arm in EVALUATION_ARMS:
        trials = [t for t in state["trials"] if t["arm"] == arm]
        for trial in trials:
            if "failed_bash_commands" not in trial:
                lines = (ARTIFACTS / "trials" / trial["label"] / "initial.jsonl").read_text().splitlines()
                trial["failed_bash_commands"] = failed_commands([json.loads(line) for line in lines if line.strip()])
        arms[arm] = {
            "passed": sum(t["passed"] for t in trials),
            "trials": len(trials),
            "cost_usd": round(sum(t["cost_usd"] or 0 for t in trials), 6),
            "bash_calls": sum(len(t["bash_commands"]) for t in trials),
            "failed_bash_calls": sum(len(t["failed_bash_commands"]) for t in trials),
            "agent_turns": sum(t["num_turns"] or 0 for t in trials),
            "input_tokens": sum((t["usage"] or {}).get("input_tokens", 0) for t in trials),
            "output_tokens": sum((t["usage"] or {}).get("output_tokens", 0) for t in trials),
            "injection_verified": bool(trials)
            and (all(t["injected_ids"] for t in trials) if arm != "baseline" else all(not t["injected_ids"] for t in trials)),
        }
    report = {
        "model": state["model"],
        "source": SOURCE,
        "arms": arms,
        "promoted": state.get("promoted", []),
        "promotion_history": state.get("promotion_history", []),
        "by_case": [
            {
                "case": case,
                "expected": grade(case, None)["expected"],
                **{
                    arm: [
                        {k: t[k] for k in ("label", "answer", "passed", "injected_ids", "bash_commands")}
                        for t in state["trials"]
                        if t["case"] == case and t["arm"] == arm
                    ]
                    for arm in EVALUATION_ARMS
                },
            }
            for case in CASES
            if case not in ("train", "long_train")
        ],
        "training": [
            {k: t.get(k) for k in ("label", "phase", "passed", "answer", "num_turns", "cost_usd")}
            for t in state["trials"]
            if t["arm"] == "training"
        ],
        "cost_caveat": "CLI cost basis is unknown. Training-session cost fields are cumulative; generator calls are not included. Do not infer billing savings from these fields.",
        "scope": "Small synthetic recreation of the published SimpleNote task, run with real Claude Code, Cortex, Evolve and model calls; not an AppWorld benchmark rerun.",
    }
    write_state(state)
    (ARTIFACTS / "summary.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2))


def main():
    global ARTIFACTS, STATE, CREDENTIALS, CORTEX, CA_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", type=Path, default=ARTIFACTS, help="Use a new directory for a fresh comparison")
    parser.add_argument("--credentials", type=Path, default=CREDENTIALS, help="Existing Claude sandbox environment file")
    parser.add_argument("--cortex-bin", type=Path, default=CORTEX, help="Cortex binary built with the Evolve plugin")
    parser.add_argument("--ca-dir", type=Path, default=CA_DIR, help="Existing trusted Cortex CA directory")
    parser.add_argument(
        "command",
        choices=[
            "preflight",
            "start",
            "learning-on",
            "learning-off",
            "trial",
            "feedback",
            "verify-training",
            "candidates",
            "mine",
            "promote",
            "warm",
            "stop",
            "summarize",
        ],
    )
    parser.add_argument("--label")
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--arm", choices=[*EVALUATION_ARMS, "training"])
    parser.add_argument("--ids", nargs="+")
    parser.add_argument("--mode", choices=["standard", "consistency"], default="consistency")
    args = parser.parse_args()
    ARTIFACTS = args.artifacts_dir.resolve()
    STATE = ARTIFACTS / "state.json"
    CREDENTIALS = args.credentials.resolve()
    CORTEX = args.cortex_bin.resolve()
    CA_DIR = args.ca_dir.resolve()
    if args.command == "preflight":
        preflight()
    elif args.command == "start":
        start()
    elif args.command in ("learning-on", "learning-off"):
        set_learning(args.command == "learning-on")
    elif args.command == "trial":
        run_trial(args.label, args.case, args.arm)
    elif args.command == "feedback":
        previous = next(t for t in reversed(read_state()["trials"]) if t["label"] == args.label)
        expected = previous["expected"]
        feedback = (
            f"Independent validation expects note_id={expected['note_id']} and completed={expected['completed']}. "
            "Check your counting procedure against the actual activity lines, excluding title and legend markers, "
            "and confirm you selected the current note. Explain the reusable procedure, then return the corrected JSON."
        )
        run_trial(args.label, previous["case"], "training", resume=previous["session_id"], prompt=feedback)
    elif args.command == "verify-training":
        previous = next(t for t in reversed(read_state()["trials"]) if t["label"] == args.label)
        prompt = (
            "Verify your corrected answer independently using the local notes service and a small script, "
            "rather than recounting in prose or relying on the expected value I supplied. "
            "Explain the reusable procedure you used and return the same two-field JSON."
        )
        run_trial(args.label, previous["case"], "training", resume=previous["session_id"], prompt=prompt, suffix="verification")
    elif args.command == "candidates":
        candidates()
    elif args.command == "mine":
        mine(args.label, args.mode)
    elif args.command == "promote":
        promote(args.ids)
    elif args.command == "warm":
        asyncio.run(warm())
    elif args.command == "stop":
        stop()
    elif args.command == "summarize":
        summarize()


if __name__ == "__main__":
    main()
