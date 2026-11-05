"""Check the recorded invoice experiment without making model calls."""

import hashlib
import gzip
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="session", autouse=True)
def phoenix_server():
    """Recorded-artifact checks do not connect to Phoenix or start a server."""
    yield None


def test_completed_invoice_learning_and_three_pairs():
    root = Path(os.environ.get("CORTEX_INVOICE_RESULTS", Path(__file__).resolve().parents[2] / "demos/cortex-invoices/results"))
    if not (root / "summary.json").is_file():
        pytest.skip("Complete the live Docker invoice experiment first.")

    def read(name):
        path = root / name
        if path.exists():
            return json.loads(path.read_text())
        with gzip.open(root / (name + ".gz"), "rt", encoding="utf-8") as stream:
            return json.load(stream)

    state = read("state.json")
    labels = list(state["sessions"])
    assert len({state["sessions"][label]["id"] for label in labels}) == len(labels)
    training = state["sessions"]["training"]["id"]
    learning = read("learning.json")
    assert learning["training_session"] == training
    assert learning["model"] == state["learning_model"]
    promoted = read("invoice-promoted.json")
    candidates = {str(row["id"]): row for row in read("invoice-candidates.json")}
    assert promoted and state["promoted"] == promoted
    for row in promoted:
        candidate = candidates[str(row["candidate_id"])]
        assert candidate["content"] == row["content"]
        assert candidate["metadata"]["session_id"] == training
        assert row["metadata"]["status"] == "active"

    manifests = {}
    for label in labels:
        session = state["sessions"][label]
        assert session["captured"]
        assert session["exit_code"] == 0
        assert session["input_hashes_before"] == session["input_hashes_after"]
        current = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / "tasks" / label / "input").glob("*.pdf")}
        assert current == session["input_hashes_before"]
        converted = read(f"{label}-trajectory.json")["trajectories"]
        assert converted and all(trajectory["batch"]["conversation_id"] == session["id"] for trajectory in converted)
        messages = [message for trajectory in converted for message in trajectory["messages"]]
        assert any(message.get("tool_calls") for message in messages)
        assert any(message.get("role") == "tool" for message in messages)
        events = read(f"{label}-cortex-session.json")["events"]
        requests = [event for event in events if event.get("phase") == "request" and (event.get("inference") or {}).get("tools")]
        first = min(requests, key=lambda event: event["seq"])
        assert {event["inference"]["model"] for event in requests} == {state["model"]}
        manifests[label] = first["inference"]["tools"]
        attribution = (first.get("plugins") or {}).get("evolve", {})
        if label.startswith("guided"):
            assert attribution["injected"] is True
            assert set(map(str, attribution["ids"])) == {str(row["id"]) for row in promoted}
            assert all(training in source for source in attribution["sources"])
            retrieval = read(f"{label}-retrieval.json")
            assert retrieval["namespace_id"] == "invoice-reviewed"
            assert retrieval["entity_ids"]
        else:
            assert not attribution.get("injected")
        if session.get("harness", {}).get("mode") == "noninteractive" or session.get("harness_transition"):
            outputs = list(root.glob(f"{label}-cli-*.json"))
            assert outputs
            for path in outputs:
                cli = json.loads(path.read_text())
                assert not cli.get("is_error")
                assert not cli.get("permission_denials")
    summary = read("summary.json")
    assert summary["completed_pairs"] == 3
    assert all(pair["input_and_injection_valid"] for pair in summary["pairs"])
    for pair in summary["pairs"]:
        assert pair["valid_comparison"] == pair["same_harness"]
    for number in (1, 2, 3):
        a, b = (state["sessions"][f"{arm}-{number}"] for arm in ("baseline", "guided"))
        assert a["prompt"] == b["prompt"]
        assert a["input_hashes_before"] == b["input_hashes_before"]
        assert manifests[f"baseline-{number}"] == manifests[f"guided-{number}"]
    repetitions = summary["same_input_repetitions"]
    if repetitions:
        assert len(repetitions) == 3
        assert all(pair["valid_comparison"] for pair in repetitions)
        for arm in ("baseline", "guided"):
            hashes = state["sessions"][f"{arm}-1"]["input_hashes_before"]
            for repetition in (2, 3):
                assert state["sessions"][f"{arm}-1-repeat-{repetition}"]["input_hashes_before"] == hashes
        for repetition in (2, 3):
            assert manifests[f"baseline-1-repeat-{repetition}"] == manifests[f"guided-1-repeat-{repetition}"]
    assert (root / "summary.html").is_file()


def test_accurate_consistency_recording_has_three_fresh_frozen_pairs():
    base = Path(__file__).resolve().parents[2] / "demos/cortex-invoices"
    root = Path(os.environ.get("CORTEX_INVOICE_CONSISTENCY_RESULTS", base / "reports/sonnet45-consistency-20261105"))
    if not (root / "summary.json").is_file():
        pytest.skip("Complete the accurate-consistency experiment first.")

    def read(name):
        path = root / name
        if path.exists():
            return json.loads(path.read_text())
        with gzip.open(root / (name + ".gz"), "rt", encoding="utf-8") as stream:
            return json.load(stream)

    summary, state, learning = (read(name) for name in ("summary.json", "state.json", "learning.json"))
    assert learning["consistency_method"] == "accurate"
    config = learning["processing_plan"]["processors"][0]["config"]
    assert config["guidelines_mode"] == "consistency" and config["consistency_method"] == "accurate"
    assert config["analysis_config"]["max_samples"] == 5
    assert config["analysis_config"]["max_steps"] == 15
    assert config["analysis_config"]["skip_on_no_uncertainty"] is True
    assert state["model"] == state["learning_model"] == "aws/claude-sonnet-4-5"
    assert summary["all_comparisons_valid"] and summary["same_tool_schemas"] and summary["frozen_selection_unchanged"]
    assert len(summary["pairs"]) == 3 and len(state["sessions"]) == 7
    assert len({row["id"] for row in state["sessions"].values()}) == 7
    assert read("trial-run.json")["status"] == "complete"
    assert read("trial-run.json")["order"] == state["trial_order"]
    candidates = {str(row["id"]): row for row in read("invoice-candidates.json")}
    promoted = read("invoice-promoted.json")
    assert state["promoted"] == promoted
    assert hashlib.sha256(json.dumps(promoted, sort_keys=True).encode()).hexdigest() == state["frozen_guidelines_sha256"]
    training = state["sessions"]["training"]["id"]
    for row in promoted:
        assert row["content"] == candidates[str(row["candidate_id"])]["content"]
        assert row["metadata"]["generation_method"] == "consistency"
        assert row["metadata"]["session_id"] == training
    for name, digest in read("training-provenance.json")["sha256"].items():
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest
        assert (root / name).read_bytes() == (base / "reports/sonnet45-20261105" / name).read_bytes()
    all_hashes, all_tools = [], []
    for pair in summary["pairs"]:
        for arm, label in pair["labels"].items():
            session = state["sessions"][label]
            assert session["harness"]["mode"] == "noninteractive" and not session.get("harness_transition")
            assert len(session["launches"]) == 1
            assert not session["launches"][0]["resume"]
            assert session["exit_code"] == 0 and session["captured"]
            hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (root / "tasks" / label / "input").glob("*.pdf")}
            assert hashes == session["input_hashes_before"] == session["input_hashes_after"]
            all_hashes.append(hashes)
            trajectory = read(f"{label}-trajectory.json")["trajectories"][0]
            assert trajectory["batch"]["conversation_id"] == session["id"]
            calls = [call for message in trajectory["messages"] for call in (message.get("tool_calls") or [])]
            assert len(calls) == pair["metrics"][arm]["tool_calls"]
            assert len({call["id"] for call in calls}) == len(calls)
            all_tools.append(trajectory["tools"])
            cli = read(f"{label}-cli-1.json")
            assert not cli.get("is_error") and not cli.get("permission_denials")
            if arm == "guided":
                retrieval = read(f"{label}-retrieval.json")
                assert retrieval["namespace_id"] == "invoice-consistency-reviewed"
                assert set(map(str, retrieval["entity_ids"])) == {str(row["id"]) for row in promoted}
                assert all(row["content"] in retrieval["text"] for row in promoted)
    assert all(value == all_hashes[0] for value in all_hashes)
    assert all(value == all_tools[0] for value in all_tools)
    assert (root / "summary.html").is_file()
