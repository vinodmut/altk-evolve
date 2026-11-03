"""Cortex session events -> OpenAI trajectories, with a per-session cursor."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import TrajectoryOutcome
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult
from altk_evolve.sync.cortex_sync import (
    CortexCursorStore,
    CortexSessionClient,
    clean_text,
    ingest_session,
    is_main_turn,
    sync_cortex,
    turns_to_messages,
)

pytestmark = pytest.mark.unit
SID = "11111111-2222-3333-4444-555555555555"
SYSTEM = {"role": "system", "content": "You are Claude Code."}
TASK = {"role": "user", "content": "<system-reminder>\nproject rules\n</system-reminder>\n\nfix the failing test"}


def turn(seq, history, completion="", calls=(), results=(), **overrides):
    """A full main-agent response event as Cortex's session API serves it."""
    inference = {
        "agentRole": "main",
        "model": "claude-opus",
        "messages": history,
        "tools": [{"name": "Bash"}],
        "completion": completion,
        "toolCalls": [{"id": cid, "name": name, "arguments": json.dumps(args)} for cid, name, args in calls],
        "toolResults": [{"toolUseId": cid, "content": content, "isError": err} for cid, content, err in results],
    }
    event = {"seq": seq, "phase": "response", "direction": "outbound", "client": {"name": "claude-code"}, "inference": inference}
    event.update(overrides)
    return event


def summary(event):
    """The ``view=summary`` projection: bodies removed, counts kept."""
    inference = {k: v for k, v in event["inference"].items() if k not in ("messages", "tools", "toolCalls", "toolResults")}
    inference["messageCount"] = len(event["inference"]["messages"])
    inference["toolCount"] = len(event["inference"]["tools"])
    return {**event, "inference": inference}


def session_events():
    h1 = [SYSTEM, TASK]
    h2 = h1 + [{"role": "assistant", "content": "Running tests."}, {"role": "user", "content": ""}]
    h3 = h2 + [{"role": "assistant", "content": ""}, {"role": "user", "content": "use uv please"}]
    return [
        {"seq": 1, "phase": "request", "inference": {"messages": h1}},
        turn(2, h1, "Running tests.", calls=[("t1", "Bash", {"command": "pytest"})]),
        turn(3, [{"role": "user", "content": "title this"}], "Fix test", tools_override=True),
        turn(5, h2, "", calls=[("t2", "Bash", {"command": "cat x.py"})], results=[("t1", "1 failed", True)]),
        turn(7, h3, "Fixed with uv.", results=[("t2", "print(1)", False)]),
        {"seq": 8, "phase": "response", "tunnel": True},
    ]


def _fix_side_call(events):
    for event in events:
        if event.get("tools_override"):
            event.pop("tools_override")
            event["inference"]["tools"] = []
    return events


class FakeCortex:
    def __init__(self, events):
        self.events = {e["seq"]: e for e in _fix_side_call(events)}
        self.updated = "2026-10-28T10:00:00-05:00"

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/sessions":
            return httpx.Response(
                200, json={"sessions": [{"id": SID, "agent": "claude-code", "updatedAt": self.updated}, {"id": "default"}]}
            )
        if path == f"/v1/sessions/{SID}":
            assert request.url.params["view"] == "summary"
            return httpx.Response(
                200,
                json={
                    "id": SID,
                    "events": [summary(e) if e.get("inference", {}).get("tools") is not None else e for e in self.events.values()],
                },
            )
        if path.startswith(f"/v1/sessions/{SID}/events/"):
            return httpx.Response(200, json=self.events[int(path.rsplit("/", 1)[1])])
        return httpx.Response(404)

    def client(self):
        return CortexSessionClient("http://cortex.test", transport=httpx.MockTransport(self.handler))


def test_clean_text_strips_injected_blocks():
    text = 'hi <evolve-guidelines source="altk-evolve" ids="a">1. x</evolve-guidelines> <system-reminder>r</system-reminder> there'
    assert clean_text(text) == "hi  <system-reminder>r</system-reminder> there"


def test_main_turn_filter():
    events = _fix_side_call(session_events())
    assert [e["seq"] for e in events if is_main_turn(e)] == [2, 5, 7]
    assert not is_main_turn(turn(9, [TASK], "x", subagent="agent-1"))
    assert not is_main_turn(turn(9, [TASK], "x", client={}))


def test_turns_to_messages_rebuilds_tool_calls_and_results():
    events = _fix_side_call(session_events())
    messages = turns_to_messages([e for e in events if is_main_turn(e)])
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "assistant", "tool", "user", "assistant"]
    assert messages[0] == SYSTEM
    assert messages[1] == TASK
    assert messages[2]["tool_calls"][0]["function"] == {"name": "Bash", "arguments": '{"command": "pytest"}'}
    assert messages[3] == {"role": "tool", "tool_call_id": "t1", "content": "[error] 1 failed"}
    assert messages[5] == {"role": "tool", "tool_call_id": "t2", "content": "print(1)"}
    assert messages[6] == {"role": "user", "content": "use uv please"}
    assert messages[7] == {"role": "assistant", "content": "Fixed with uv."}


def test_turns_to_messages_resets_after_compaction():
    first = turn(2, [SYSTEM, TASK, {"role": "assistant", "content": "a"}, {"role": "user", "content": "more"}], "ok")
    compacted = turn(4, [SYSTEM, {"role": "user", "content": "summary of earlier work"}], "continuing")
    messages = turns_to_messages([first, compacted])
    assert {"role": "user", "content": "summary of earlier work"} in messages


@pytest.fixture
def evolve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    from altk_evolve.config.guidelines import guidelines_settings

    monkeypatch.setattr(guidelines_settings, "guidelines_mode", "standard")
    seen = []

    def generate(messages, *, context_messages=None, options, outcome=None, tools=None, execution_metadata=None):
        seen.append((messages, context_messages))
        return [
            GuidelineGenerationResult(
                task_description="fix test",
                guidelines=[
                    Guideline(content=f"Use uv {len(seen)}", rationale="r", category="strategy", trigger="t", implementation_steps=[])
                ],
            )
        ]

    def resolve(candidates, incoming, settings=None):
        return [EntityUpdate(id=e.id, type=e.type, content=e.content, event="ADD") for e in incoming]

    monkeypatch.setattr("altk_evolve.llm.guidelines.guidelines.generate_guidelines", generate)
    monkeypatch.setattr("altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts", resolve)
    client = EvolveClient(EvolveConfig(settings=FilesystemSettings(data_dir=str(tmp_path / "entities"))))
    return client, seen


def test_dry_run_converts_without_processing_or_moving_cursor(tmp_path):
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    result = ingest_session(None, FakeCortex(session_events()).client(), SID, namespace_id="cortex", cursors=cursors, dry_run=True)
    assert result.status == "dry-run" and (result.first_seq, result.last_seq) == (2, 7)
    trajectory = result.trajectory
    assert trajectory["batch"] == {**trajectory["batch"], "source": "cortex", "conversation_id": SID, "batch_id": "2-7", "scope": "cortex"}
    assert (
        trajectory["metadata"].items()
        >= {"status": "candidate", "creation_mode": "auto-cortex", "agent_id": "claude-code", "session_id": SID}.items()
    )
    assert trajectory["metadata"]["cortex_capture"]["adapter_revision"] == 2
    assert trajectory["tools"][0]["function"]["name"] == "Bash"
    assert cursors.get(SID) == 0


def test_ingest_writes_candidates_and_advances_cursor(tmp_path, evolve):
    client, seen = evolve
    cortex = FakeCortex(session_events())
    cursors = CortexCursorStore(tmp_path / "cursors.json")

    result = ingest_session(client, cortex.client(), SID, namespace_id="cortex", cursors=cursors)

    assert result.status == "processed" and len(result.guidelines) == 1
    [guideline] = client.get_all_entities("cortex", filters={"type": "guideline"})
    assert guideline.metadata["status"] == "candidate"
    assert guideline.metadata["creation_mode"] == "auto-cortex"
    assert guideline.metadata["session_id"] == SID
    assert guideline.metadata["source_task_id"] == f"cortex:{SID}:7"
    assert cursors.get(SID) == 7
    assert client.select_guidelines("cortex", "fix test", statuses={"active"}).all == []

    again = ingest_session(client, cortex.client(), SID, namespace_id="cortex", cursors=cursors)
    assert again.status == "up-to-date" and len(seen) == 1


def test_later_turns_learn_only_new_material_with_task_context(tmp_path, evolve):
    client, seen = evolve
    events = session_events()
    cortex = FakeCortex(events[:3])  # first turn only (plus the side call)
    cursors = CortexCursorStore(tmp_path / "cursors.json")

    assert ingest_session(client, cortex.client(), SID, namespace_id="cortex", cursors=cursors, min_new_turns=1).status == "processed"
    cortex.events.update({e["seq"]: e for e in events[3:]})
    result = ingest_session(client, cortex.client(), SID, namespace_id="cortex", cursors=cursors)

    assert (result.first_seq, result.last_seq) == (5, 7)
    messages, context = seen[-1]
    assert context[0] == {"role": "user", "content": "fix the failing test"}
    assert any(m.get("tool_calls", [{}])[0].get("id") == "t1" for m in context)
    assert messages[0] == {"role": "tool", "tool_call_id": "t1", "content": "[error] 1 failed"}
    assert all(m.get("content") != "fix the failing test" for m in messages)


def test_too_few_new_turns_keeps_cursor(tmp_path):
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    cortex = FakeCortex(session_events()[:3])
    result = ingest_session(None, cortex.client(), SID, namespace_id="cortex", cursors=cursors)
    assert result.status == "too-few-turns" and cursors.get(SID) == 0


def test_sync_selects_idle_sessions(tmp_path):
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    cortex = FakeCortex(session_events())
    busy = sync_cortex(
        None,
        cortex.client(),
        namespace_id="cortex",
        cursors=cursors,
        idle_minutes=15,
        dry_run=True,
        now=datetime(2026, 10, 28, 15, 5, tzinfo=UTC),
    )
    assert busy == []
    idle = sync_cortex(
        None,
        cortex.client(),
        namespace_id="cortex",
        cursors=cursors,
        idle_minutes=15,
        dry_run=True,
        now=datetime(2026, 10, 28, 16, 0, tzinfo=UTC),
    )
    assert [(r.session_id, r.status) for r in idle] == [(SID, "dry-run")]


def test_sync_reports_errors_per_session(tmp_path):
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    broken = CortexSessionClient("http://cortex.test", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    [result] = sync_cortex(None, broken, namespace_id="cortex", cursors=cursors, session_ids=[SID])
    assert result.status.startswith("error")


def test_same_length_history_edit_is_not_lost():
    before = turn(2, [SYSTEM, {"role": "user", "content": "Use account OLD"}], "ok")
    after = turn(4, [SYSTEM, {"role": "user", "content": "Correction: use account NEW"}], "retry")
    messages = turns_to_messages([before, after])
    assert {"role": "user", "content": "Correction: use account NEW"} in messages
    assert messages.count(SYSTEM) == 1


def test_changing_injected_guidelines_does_not_replay_old_input():
    histories = [[{"role": "system", "content": f'<evolve-guidelines ids="{i}">own output</evolve-guidelines>Rules'}, TASK] for i in (1, 2)]
    messages = turns_to_messages([turn(2, histories[0], "first"), turn(4, histories[1], "second")])
    assert messages.count(TASK) == 1
    assert "evolve-guidelines" not in json.dumps(messages)


def test_delayed_parallel_results_survive_intermediate_turns():
    calls = [("z", "Bash", {"command": "long job"}), ("a", "Bash", {"command": "short job"})]
    events = [
        turn(2, [TASK], calls=calls),
        turn(4, [TASK], "Still waiting", results=[("a", "short result", False)]),
        turn(6, [TASK], "Done", results=[("a", "short result", False), ("z", "long result", False)]),
    ]
    results = [m for m in turns_to_messages(events) if m["role"] == "tool"]
    assert [(m["tool_call_id"], m["content"]) for m in results] == [("a", "short result"), ("z", "long result")]


def test_full_outputs_and_user_constraints_survive_conversion():
    task = {"role": "user", "content": "x" * 12000 + " REQUIRED_FINAL_CONSTRAINT"}
    output = "header " + "x" * 10000 + " FINAL_FAILURE"
    events = [turn(2, [task], calls=[("a", "Bash", {})]), turn(4, [task], results=[("a", output, True)])]
    messages = turns_to_messages(events)
    assert messages[0] == task
    assert next(m["content"] for m in messages if m["role"] == "tool") == "[error] " + output


def test_unpaired_outputs_are_preserved_without_inventing_calls():
    event = turn(2, [TASK], "Observed", results=[("unknown", "artifact is invalid", True)])
    messages = turns_to_messages([event])
    assert {"role": "tool", "tool_call_id": "unknown", "content": "[error] artifact is invalid"} in messages
    assert not any(m.get("tool_calls") for m in messages)


def test_request_tool_messages_are_not_duplicated():
    event = turn(2, [TASK, {"role": "tool", "toolUseId": "a", "content": "observed"}], results=[("a", "observed", False)])
    messages = turns_to_messages([event])
    assert len([m for m in messages if m["role"] == "tool"]) == 1


def test_incomplete_inference_is_visible_as_evidence():
    event = turn(2, [TASK], "partial output", statusCode=502, error={"message": "upstream disconnected"})
    event["inference"]["finishReason"] = "max_tokens"
    messages = turns_to_messages([event])
    assert "max_tokens" in messages[-1]["content"]
    assert "upstream disconnected" in messages[-1]["content"]


def _many_turns(count):
    history = [SYSTEM, TASK]
    events = []
    for i in range(count):
        events.append(turn(2 * (i + 1), list(history), f"Action {i}"))
        history.append({"role": "assistant", "content": f"Action {i}"})
    return events


def test_backlog_is_drained_without_skipping_oldest_turns(tmp_path, evolve):
    client, seen = evolve
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    result = ingest_session(client, FakeCortex(_many_turns(5)).client(), SID, namespace_id="cortex", cursors=cursors, max_turns=2)
    assert (result.first_seq, result.last_seq, result.new_turns) == (2, 10, 5)
    assert [[m["content"] for m in batch if m["role"] == "assistant"] for batch, _ in seen] == [
        ["Action 0", "Action 1"],
        ["Action 2", "Action 3"],
        ["Action 4"],
    ]
    assert cursors.get(SID) == 10


def test_failed_batch_checkpoints_only_completed_work_and_retries_single_turn(tmp_path, evolve, monkeypatch):
    client, seen = evolve
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    cortex = FakeCortex(_many_turns(5)).client()
    process = client.process_trajectory
    attempts = 0

    def fail_last(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 3:
            raise RuntimeError("temporary processor failure")
        return process(*args, **kwargs)

    monkeypatch.setattr(client, "process_trajectory", fail_last)
    with pytest.raises(RuntimeError, match="temporary"):
        ingest_session(client, cortex, SID, namespace_id="cortex", cursors=cursors, max_turns=2)
    assert cursors.get(SID) == 8
    retried = ingest_session(client, cortex, SID, namespace_id="cortex", cursors=cursors, max_turns=2)
    assert (retried.first_seq, retried.last_seq) == (10, 10)
    assert len(seen) == 3 and cursors.get(SID) == 10


def test_dry_run_exposes_every_batch_without_advancing_cursor(tmp_path):
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    result = ingest_session(
        None, FakeCortex(_many_turns(5)).client(), SID, namespace_id="cortex", cursors=cursors, max_turns=2, dry_run=True
    )
    assert [t["batch"]["batch_id"] for t in result.trajectories] == ["2-4", "6-8", "10-10"]
    assert result.trajectory == result.trajectories[0]
    assert cursors.get(SID) == 0


def test_original_task_and_tool_definitions_survive_compaction(tmp_path):
    events = _many_turns(3)
    events[-1]["inference"]["messages"] = [SYSTEM, {"role": "user", "content": "Compacted work summary"}]
    events[-1]["inference"]["tools"] = [{"name": "Bash", "description": "Run commands with uv", "parameters": {"type": "object"}}]
    events[-1]["inference"].update({"finishReason": "end_turn", "totalTokens": 1234})
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    cursors.set(SID, 4)
    result = ingest_session(None, FakeCortex(events).client(), SID, namespace_id="cortex", cursors=cursors, dry_run=True)
    trajectory = result.trajectory
    assert trajectory["context_messages"][0]["content"] == "fix the failing test"
    assert trajectory["tools"][0]["function"]["description"] == "Run commands with uv"
    assert trajectory["metadata"]["cortex_capture"]["turns"][0]["inference"]["totalTokens"] == 1234
    assert {"role": "user", "content": "Compacted work summary"} in trajectory["messages"]


def test_late_evaluator_feedback_replays_once_with_new_revision(tmp_path, evolve):
    client, seen = evolve
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    cortex = FakeCortex(_many_turns(2)).client()
    ingest_session(client, cortex, SID, namespace_id="cortex", cursors=cursors)
    outcome = TrajectoryOutcome(success=False, failed_checks=("revenue must reconcile",), detail="Ledger total mismatch")
    result = ingest_session(client, cortex, SID, namespace_id="cortex", cursors=cursors, outcome=outcome)
    assert result.status == "processed" and result.new_turns == 0 and len(seen) == 2
    assert any(e.metadata.get("evidence") == "failure" for e in client.get_all_entities("cortex", filters={"type": "guideline"}))
    ingest_session(client, cortex, SID, namespace_id="cortex", cursors=cursors, outcome=outcome)
    assert len(seen) == 2 and cursors.get(SID) == 4


def test_last_tool_output_in_request_survives_missing_response(tmp_path):
    first = turn(2, [TASK], calls=[("a", "Bash", {"command": "validate"})])
    pending = turn(3, [TASK, {"role": "assistant", "content": ""}], results=[("a", "FINAL_TOOL_FAILURE", True)], phase="request")
    result = ingest_session(
        None,
        FakeCortex([first, pending]).client(),
        SID,
        namespace_id="cortex",
        cursors=CortexCursorStore(tmp_path / "cursors.json"),
        dry_run=True,
    )
    assert (result.first_seq, result.last_seq) == (2, 3)
    messages = result.trajectory["messages"]
    assert any(m["role"] == "tool" and "FINAL_TOOL_FAILURE" in m["content"] for m in messages)
    assert "no response was captured yet" in messages[-1]["content"]
    assert len([m for m in messages if m["role"] == "assistant"]) == 1


def test_subagent_evidence_is_preserved_without_becoming_main_agent_actions(tmp_path):
    first = turn(2, [TASK], "Delegating", calls=[("delegate", "Task", {"prompt": "Find the ledger contract"})])
    child = turn(
        4,
        [{"role": "user", "content": "Investigate refunds"}],
        "CHILD_FINDING: subtract refunds",
        calls=[("child-call", "Bash", {"command": "ledger --help"})],
        subagent="child-1",
    )
    last = turn(6, [TASK], "Applying the finding", results=[("delegate", "Summary returned", False)])
    result = ingest_session(
        None,
        FakeCortex([first, child, last]).client(),
        SID,
        namespace_id="cortex",
        cursors=CortexCursorStore(tmp_path / "cursors.json"),
        dry_run=True,
    )
    messages = result.trajectory["messages"]
    observation = next(m for m in messages if "Cortex subagent observation" in (m.get("content") or ""))
    assert observation["role"] == "system" and "CHILD_FINDING" in observation["content"]
    assert "ledger --help" in observation["content"] and "not the main agent's user instructions" in observation["content"]
    assert len([m for m in messages if m["role"] == "assistant"]) == 2
    assert result.trajectory["metadata"]["cortex_capture"]["subagent_seqs"] == [4]


def test_cli_dry_run_exposes_all_batches(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from altk_evolve.cli.cli import app
    from altk_evolve.sync import cortex_sync

    cortex = FakeCortex(_many_turns(41)).client()
    cursors = CortexCursorStore(tmp_path / "cursors.json")
    monkeypatch.setattr(cortex_sync, "CortexSessionClient", lambda *args, **kwargs: cortex)
    monkeypatch.setattr(cortex_sync, "default_cursor_store", lambda: cursors)
    invocation = CliRunner().invoke(app, ["sync", "cortex", "--session", SID, "--dry-run"])
    assert invocation.exit_code == 0, invocation.output
    payload = json.loads(invocation.stdout)
    assert [t["batch"]["batch_id"] for t in payload["trajectories"]] == ["2-80", "82-82"]
    assert payload["trajectory"] == payload["trajectories"][0]
    assert cursors.get(SID) == 0
