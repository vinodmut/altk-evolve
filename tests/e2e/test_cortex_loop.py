"""The Cortex loop through the HTTP surface: idle notification -> candidate -> promote -> served -> retired.

The LLM is stubbed and Cortex is faked, so the test is hermetic; it exercises the real REST
router, cursor store, processing pipeline, filesystem backend and CLI together.
"""

import httpx
import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from altk_evolve.cli.cli import app as cli
from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.api import cortex as cortex_api
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.conflict_resolution import EntityUpdate
from altk_evolve.schema.guidelines import Guideline, GuidelineGenerationResult
from altk_evolve.sync import cortex_sync

pytestmark = pytest.mark.e2e
SID = "aaaaaaaa-0000-0000-0000-000000000001"
TOKEN = "e2e-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture(scope="module")
def phoenix_server():
    """This hermetic HTTP/CLI loop does not use the global live-Phoenix fixture."""
    return None


def _turn(seq, history, completion, calls=()):
    return {
        "seq": seq,
        "phase": "response",
        "client": {"name": "claude-code"},
        "inference": {
            "agentRole": "main",
            "messages": history,
            "tools": [{"name": "Bash"}],
            "completion": completion,
            "toolCalls": [{"id": c, "name": "Bash", "arguments": "{}"} for c in calls],
        },
    }


def _fake_cortex():
    task = [{"role": "user", "content": "run the tests"}]
    events = {
        2: _turn(2, task, "Running.", calls=["t1"]),
        4: _turn(4, task + [{"role": "assistant", "content": "Running."}, {"role": "user", "content": ""}], "All tests pass."),
    }
    events[4]["inference"]["toolResults"] = [{"toolUseId": "t1", "content": "TOOL_OBSERVATION: 24 tests passed"}]

    def handler(request):
        path = request.url.path
        if path == f"/v1/sessions/{SID}":
            return httpx.Response(
                200,
                json={
                    "events": [
                        {**e, "inference": {**e["inference"], "messageCount": len(e["inference"]["messages"]), "toolCount": 1}}
                        for e in events.values()
                    ]
                },
            )
        if path.startswith(f"/v1/sessions/{SID}/events/"):
            return httpx.Response(200, json=events[int(path.rsplit("/", 1)[1])])
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    monkeypatch.setenv("EVOLVE_API_TOKEN", TOKEN)
    from altk_evolve.config.guidelines import guidelines_settings

    monkeypatch.setattr(guidelines_settings, "guidelines_mode", "standard")

    def generate(messages, *, context_messages=None, options, outcome=None):
        from altk_evolve.llm.guidelines.guidelines import parse_openai_agents_trajectory

        rendered = parse_openai_agents_trajectory(messages, options=options)
        assert "TOOL_OBSERVATION: 24 tests passed" in rendered["trajectory_summary"]
        return [
            GuidelineGenerationResult(
                task_description="run the tests",
                guidelines=[Guideline(content="Run tests with uv run pytest -v", rationale="r", category="strategy", trigger="t")],
            )
        ]

    monkeypatch.setattr("altk_evolve.llm.guidelines.guidelines.generate_guidelines", generate)
    monkeypatch.setattr(
        "altk_evolve.llm.conflict_resolution.conflict_resolution.resolve_conflicts",
        lambda candidates, incoming, settings=None: [EntityUpdate(id=e.id, type=e.type, content=e.content, event="ADD") for e in incoming],
    )
    client = EvolveClient(EvolveConfig(settings=FilesystemSettings(data_dir=str(tmp_path / "data"))))
    transport = _fake_cortex()
    monkeypatch.setattr(cortex_api, "_client", lambda: client)
    monkeypatch.setattr(cortex_api, "_namespace", lambda: "cortex")
    monkeypatch.setattr(cortex_api, "run_in_background", lambda fn: fn())
    monkeypatch.setattr(cortex_sync, "default_cursor_store", lambda: cortex_sync.CortexCursorStore(tmp_path / "cursors.json"))
    original = cortex_sync.CortexSessionClient.__init__
    monkeypatch.setattr(
        cortex_sync.CortexSessionClient,
        "__init__",
        lambda self, base_url=None, **kw: original(self, "http://cortex.test", transport=transport),
    )
    monkeypatch.setattr("altk_evolve.cli.cli.get_client", lambda: client)
    return client, TestClient(cortex_api.create_app())


def _served(api):
    response = api.post("/api/cortex/guidelines", json={"task": "run the tests"}, headers=AUTH)
    assert response.status_code == 200
    return [g["content"] for g in response.json()["guidelines"]]


def test_cortex_loop_gates_on_review_and_rolls_back(loop):
    client, api = loop

    assert api.post(f"/api/cortex/sessions/{SID}/ingest", json={"agent": "claude-code"}, headers=AUTH).status_code == 202
    [guideline] = client.get_all_entities("cortex", filters={"type": "guideline"})
    assert guideline.metadata["status"] == "candidate"
    assert _served(api) == []  # learned, but not yet reviewed

    runner = CliRunner()
    assert runner.invoke(cli, ["entities", "promote", "cortex", guideline.id, "--reason", "ok"]).exit_code == 0
    assert _served(api) == ["Run tests with uv run pytest -v"]

    # A repeated notification learns nothing new.
    api.post(f"/api/cortex/sessions/{SID}/ingest", headers=AUTH)
    assert len(client.get_all_entities("cortex", filters={"type": "guideline"})) == 1

    assert runner.invoke(cli, ["entities", "retire", "cortex", guideline.id, "--reason", "stale"]).exit_code == 0
    assert _served(api) == []
    assert client.get_entity_by_id("cortex", guideline.id).metadata["status"] == "retired"
