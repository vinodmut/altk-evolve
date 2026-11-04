"""Verify evidence produced by the real interactive Docker walkthrough.

Run the walkthrough first, then run this test with -m e2e. It makes no new
model calls and skips if the completed live-run artifacts are unavailable.
"""

import hashlib
import json
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


def test_completed_interactive_cortex_evolve_photo_loop():
    root = Path(os.environ.get("CORTEX_PHOTO_DEMO_RESULTS", Path(__file__).resolve().parents[2] / "demos/cortex-photo/results"))
    if not (root / "comparison.json").exists():
        pytest.skip("Run demos/cortex-photo's interactive Docker walkthrough first.")

    def read(name):
        return json.loads((root / name).read_text())

    state = read("state.json")
    sessions = state["sessions"]
    assert len({sessions[arm]["id"] for arm in ("baseline", "training", "guided")}) == 3
    assert sessions["baseline"]["prompt"] == sessions["guided"]["prompt"]
    photo_hashes = {hashlib.sha256((root / "photos" / arm / "sample.jpg").read_bytes()).hexdigest() for arm in sessions}
    assert photo_hashes == {"15f46ed3db89d27635c4a1f1c85616bdcac2ae96d3245e53426ee1e7c55d6353"}

    for arm, session in sessions.items():
        trajectories = read(f"{arm}-trajectory.json")["trajectories"]
        assert trajectories
        assert all(t["batch"]["conversation_id"] == session["id"] for t in trajectories)
        assert any(m.get("tool_calls") for t in trajectories for m in t["messages"])
        assert any(m.get("role") == "tool" for t in trajectories for m in t["messages"])

    assert read("learning.json")["training_session"] == sessions["training"]["id"]
    candidates = {str(row["id"]): row for row in read("photo-candidates.json")}
    promoted = read("photo-promoted.json")
    assert promoted
    for row in promoted:
        candidate = candidates[str(row["candidate_id"])]
        assert row["content"] == candidate["content"]
        assert candidate["metadata"]["session_id"] == sessions["training"]["id"]
        assert row["metadata"]["status"] == "active"
        source_ids = {source["conversation_id"] for source in row["metadata"]["sources"]}
        assert source_ids == {sessions["training"]["id"]}

    def first_main_request(arm):
        events = sorted(read(f"{arm}-cortex-session.json")["events"], key=lambda event: event["seq"])
        return next(e for e in events if e.get("phase") == "request" and (e.get("inference") or {}).get("tools"))

    assert not (first_main_request("baseline").get("plugins") or {}).get("evolve", {}).get("injected")
    attribution = first_main_request("guided")["plugins"]["evolve"]
    assert attribution["injected"] is True
    assert set(map(str, attribution["ids"])) <= {str(row["id"]) for row in promoted}
    assert attribution["ids"]
    assert all(sessions["training"]["id"] in source for source in attribution["sources"])
    retrieval = read("photo-retrieval.json")
    assert retrieval["namespace_id"] == "photo-reviewed" and retrieval["entity_ids"]
    assert read("comparison.json")["reviewed_ids_injected"] is True
    assert (root / "comparison.html").is_file()
