"""The Cortex REST surface: bearer auth, active-only retrieval, asynchronous ingest."""

import pytest
from fastapi.testclient import TestClient

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.filesystem import FilesystemSettings
from altk_evolve.frontend.api import cortex as cortex_api
from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.schema.core import Entity

pytestmark = pytest.mark.unit
TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def evolve(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("EVOLVE_HOOKS_CONFIG", "")
    monkeypatch.delenv("EVOLVE_API_TOKEN", raising=False)
    monkeypatch.delenv("EVOLVE_API_TOKEN_FILE", raising=False)
    client = EvolveClient(EvolveConfig(settings=FilesystemSettings(data_dir=str(tmp_path / "entities"))))
    monkeypatch.setattr(cortex_api, "_client", lambda: client)
    monkeypatch.setattr(cortex_api, "_namespace", lambda: "cortex")
    return client


@pytest.fixture
def api(evolve, monkeypatch):
    monkeypatch.setenv("EVOLVE_API_TOKEN", TOKEN)
    return TestClient(cortex_api.create_app())


def test_refuses_to_serve_without_a_configured_token(evolve):
    api = TestClient(cortex_api.create_app())
    assert api.post("/api/cortex/guidelines", json={"task": "x"}, headers=AUTH).status_code == 503
    assert api.get("/healthz").status_code == 200


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}, {"Authorization": TOKEN}, {"Authorization": f"Basic {TOKEN}"}])
def test_rejects_missing_or_wrong_token(api, headers):
    response = api.post("/api/cortex/guidelines", json={"task": "x"}, headers=headers)
    assert response.status_code == 401


def test_token_file(evolve, monkeypatch, tmp_path):
    path = tmp_path / "token"
    path.write_text(f"{TOKEN}\n")
    monkeypatch.setenv("EVOLVE_API_TOKEN_FILE", str(path))
    api = TestClient(cortex_api.create_app())
    assert api.post("/api/cortex/guidelines", json={"task": "x"}, headers=AUTH).status_code == 200


def test_missing_namespace_returns_no_guidelines(api):
    response = api.post("/api/cortex/guidelines", json={"task": "x"}, headers=AUTH)
    assert response.json() == {"namespace_id": "cortex", "guidelines": []}


def test_serves_active_guidelines_only(api, evolve):
    evolve.create_namespace("cortex")
    evolve.update_entities(
        "cortex",
        [
            Entity(
                type="guideline", content="active one", metadata={"status": "active", "rationale": "why", "creation_mode": "auto-cortex"}
            ),
            Entity(type="guideline", content="candidate one", metadata={"status": "candidate"}),
            Entity(type="guideline", content="retired one", metadata={"status": "retired"}),
        ],
        enable_conflict_resolution=False,
    )
    response = api.post("/api/cortex/guidelines", json={"task": "do things", "session_id": "s", "agent": "claude-code"}, headers=AUTH)
    assert response.status_code == 200
    [guideline] = response.json()["guidelines"]
    assert guideline["content"] == "active one"
    assert guideline["rationale"] == "why" and guideline["creation_mode"] == "auto-cortex" and guideline["id"]


def test_rejects_unknown_request_fields(api):
    assert api.post("/api/cortex/guidelines", json={"task": "x", "bogus": 1}, headers=AUTH).status_code == 422


def test_ingest_is_accepted_and_runs_in_background(api, monkeypatch):
    started, ingested = [], []
    monkeypatch.setattr(cortex_api, "run_in_background", lambda fn: (started.append(fn), fn()))
    monkeypatch.setattr(cortex_api, "_ingest", lambda sid: ingested.append(sid))

    response = api.post("/api/cortex/sessions/abc/ingest", json={"agent": "claude-code"}, headers=AUTH)

    assert response.status_code == 202 and response.json() == {"session_id": "abc", "accepted": True}
    assert len(started) == 1 and ingested == ["abc"]
    assert api.post("/api/cortex/sessions/abc/ingest").status_code == 401


def test_ingest_accepts_explicit_evaluator_feedback(api, monkeypatch):
    ingested = []
    monkeypatch.setattr(cortex_api, "run_in_background", lambda fn: fn())
    monkeypatch.setattr(cortex_api, "_ingest", lambda sid, **kwargs: ingested.append((sid, kwargs["outcome"])))
    response = api.post(
        "/api/cortex/sessions/abc/ingest",
        headers=AUTH,
        json={"outcome": {"success": False, "failed_checks": ["ledger must reconcile"], "detail": "Actual 90, expected 100"}},
    )
    assert response.status_code == 202
    assert ingested[0][1].success is False and ingested[0][1].failed_checks == ("ledger must reconcile",)
    assert (
        api.post("/api/cortex/sessions/abc/ingest", headers=AUTH, json={"outcome": {"success": False, "bogus": "ignore this"}}).status_code
        == 422
    )
