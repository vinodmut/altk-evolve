"""Check the recorded invoice experiment without making model calls."""

import hashlib
import gzip
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


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
