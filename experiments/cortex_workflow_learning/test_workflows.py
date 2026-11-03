"""Service boundary and independent end-state checks, before live model calls."""

import copy
import json
from types import SimpleNamespace

import pytest

from . import ops, tasks
from .bridge import flatten_system
from . import ledger_tasks

pytestmark = pytest.mark.unit


def test_learning_analysis_identity_separates_objectives_and_feedback():
    from .run import analysis_revision

    original = analysis_revision("2", "standard", "correctness", {"success": True})
    assert original == analysis_revision("2", "standard", "correctness", {"success": True})
    assert (
        len(
            {
                original,
                analysis_revision("2", "standard", "fewer-errors", {"success": True}),
                analysis_revision("2", "consistency", "correctness", {"success": True}),
                analysis_revision("2", "standard", "correctness", {"success": False}),
            }
        )
        == 4
    )


def test_learning_prompt_recording_excludes_transport_secrets_and_restores_on_error(tmp_path, monkeypatch):
    from . import run

    modules = [SimpleNamespace(completion=lambda **kwargs: kwargs["messages"]) for _ in range(2)]
    originals = [module.completion for module in modules]
    monkeypatch.setattr(run.importlib, "import_module", lambda name: modules[int(name.endswith("consistency_guidelines"))])
    path = tmp_path / "prompts.jsonl"
    messages = [{"role": "user", "content": "Task evidence"}]
    with pytest.raises(RuntimeError, match="probe"):
        with run.record_learning_prompts(path):
            assert (
                modules[0].completion(model="test", messages=messages, api_key="secret-value", headers={"Authorization": "secret"})
                == messages
            )
            raise RuntimeError("probe")
    assert [module.completion for module in modules] == originals
    assert json.loads(path.read_text()) == {"purpose": "guidelines", "model": "test", "messages": messages}
    assert "secret" not in path.read_text()


def test_learning_processes_every_captured_batch(tmp_path, monkeypatch):
    from altk_evolve.frontend.client import evolve_client
    from altk_evolve.sync import cortex_sync
    from . import run

    batches = [
        {
            "batch": {"source": "cortex", "conversation_id": "training", "batch_id": str(index), "revision": "2"},
            "messages": [{"role": "user", "content": f"batch {index}"}],
        }
        for index in range(2)
    ]
    metric = {"label": "train", "case": "ledger_train", "session_id": "training", "checks": {"saved": True}, "passed": True, "expected": {}}
    state = {"model": "test", "trials": [metric]}
    processed = []

    class Client:
        def ensure_namespace(self, namespace):
            pass

        def process_trajectory(self, trajectory, **kwargs):
            processed.append(trajectory)
            return SimpleNamespace(updates=[], skipped_processors=[])

    monkeypatch.setattr(run.driver, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(run.driver, "read_state", lambda: state)
    monkeypatch.setattr(run.driver, "write_state", lambda value: None)
    monkeypatch.setattr(run.driver, "base_env", lambda model: {})
    monkeypatch.setattr(evolve_client, "EvolveClient", Client)
    monkeypatch.setattr(cortex_sync, "CortexSessionClient", lambda url: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(cortex_sync, "ingest_session", lambda *args, **kwargs: SimpleNamespace(trajectory=batches[0], trajectories=batches))
    run.learn("train", "standard", "correctness")
    assert [trajectory.batch.batch_id for trajectory in processed] == ["0", "1"]
    assert all(trajectory.outcome.success for trajectory in processed)
    assert len(json.loads((tmp_path / "learning-train-standard-correctness.inputs.json").read_text())) == 2


def test_timeout_preserves_partial_output_and_cannot_pass(tmp_path, monkeypatch):
    import subprocess

    from ..cortex_learning_demo import run as driver

    state = {"model": "test-model", "trials": []}
    partial = (
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "call-1",
                            "name": "Bash",
                            "input": {"command": "python3 ops.py --help"},
                        }
                    ]
                },
            }
        )
        + "\n"
    )
    monkeypatch.setattr(driver, "read_state", lambda: state)
    monkeypatch.setattr(driver, "write_state", lambda value: None)
    monkeypatch.setattr(driver, "base_env", lambda model: {})
    monkeypatch.setattr(driver, "prepare_trial", lambda *args: tmp_path)
    monkeypatch.setattr(driver, "session_evidence", lambda sid: [])
    # Even a correct on-disk artifact does not mean a timed-out job completed.
    monkeypatch.setattr(driver, "grade", lambda *args: {"passed": True, "checks": {"saved_report": True}, "expected": {}})

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("claude", 240, output=partial.encode(), stderr=b"partial diagnostic")

    monkeypatch.setattr(driver.subprocess, "run", timeout)
    result = driver.run_trial("timeout", "train", "training")
    assert result["timed_out"] and not result["passed"]
    assert result["num_turns"] == 1
    assert result["usage"] is None
    assert (tmp_path / "initial.jsonl").read_text() == partial
    assert (tmp_path / "initial.stderr.log").read_text() == "partial diagnostic"
    assert state["trials"][0]["checks"]["completed_within_time_limit"] is False


@pytest.mark.parametrize(
    "arm,inject,promoted,message",
    [
        ("baseline", True, True, "Disable injection"),
        ("baseline", False, True, "reached preparation"),
        ("evolve", False, True, "Enable injection"),
        ("evolve", True, False, "Review and promote"),
    ],
)
def test_evaluation_cannot_silently_use_the_wrong_injection_arm(tmp_path, monkeypatch, arm, inject, promoted, message):
    import yaml

    from ..cortex_learning_demo import run as driver

    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "cortex.yaml").write_text(
        yaml.safe_dump({"pipeline": {"outbound": {"plugins": [{"name": "evolve", "config": {"learn": False, "inject": inject}}]}}})
    )
    monkeypatch.setattr(driver, "ARTIFACTS", tmp_path)
    monkeypatch.setattr(driver, "read_state", lambda: {"promoted": [{"id": "1"}] if promoted else []})

    def preparation(*args):
        raise RuntimeError("reached preparation")

    monkeypatch.setattr(driver, "prepare_trial", preparation)
    with pytest.raises(RuntimeError, match=message):
        driver.run_trial("test", "a", arm)


def test_bridge_preserves_all_system_text_and_request_fields():
    body = {
        "system": [
            {"type": "text", "text": "Agent instructions", "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": "Environment instructions"},
            {"type": "text", "text": "<evolve-guidelines>Learned procedure</evolve-guidelines>"},
        ],
        "messages": [{"role": "user", "content": "Export invoices"}],
        "tools": [{"name": "Bash"}],
        "stream": True,
    }
    changed = flatten_system(body)
    assert changed["system"] == "Agent instructions\n\nEnvironment instructions\n\n<evolve-guidelines>Learned procedure</evolve-guidelines>"
    assert {key: value for key, value in changed.items() if key != "system"} == {
        key: value for key, value in body.items() if key != "system"
    }
    assert isinstance(body["system"], list)
    assert flatten_system(changed) == changed
    with pytest.raises(ValueError, match="text system blocks"):
        flatten_system({"system": [{"type": "image"}]})


def test_bridge_forwards_stream_and_authorization(monkeypatch):
    import httpx
    from fastapi.testclient import TestClient

    from . import bridge

    monkeypatch.setenv("WORKFLOW_REAL_GATEWAY", "https://gateway.invalid")
    stream = b'event: message_start\ndata: {"type":"message_start"}\n\nevent: message_stop\ndata: {"type":"message_stop"}\n\n'

    async def upstream(request):
        assert str(request.url) == "https://gateway.invalid/v1/messages"
        assert request.headers["x-api-key"] == "synthetic-test-key"
        payload = json.loads(request.content)
        assert payload["system"] == "Agent\n\nLearned lesson"
        assert payload["messages"] == [{"role": "user", "content": "Task"}]
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(bridge.httpx, "AsyncClient", lambda **kwargs: real_client(transport=httpx.MockTransport(upstream), **kwargs))
    with TestClient(bridge.app) as client:
        response = client.post(
            "/v1/messages",
            headers={"x-api-key": "synthetic-test-key"},
            json={
                "system": [{"type": "text", "text": "Agent"}, {"type": "text", "text": "Learned lesson"}],
                "messages": [{"role": "user", "content": "Task"}],
                "stream": True,
            },
        )
    assert response.status_code == 200
    assert response.content == stream
    assert response.headers["content-type"].startswith("text/event-stream")


def test_pagination_and_remaining_refunds_are_required(tmp_path):
    initial = copy.deepcopy(tasks.CASES["refund_train"]["state"])
    customer = "customer-101"
    first = ops.perform(initial, "charges", SimpleNamespace(customer=customer, cursor=None))
    assert first["next_cursor"] is not None
    assert len(first["items"]) < len(initial["charges"])
    remaining = tasks.expected_refunds(initial)
    assert len(remaining) == 2
    with pytest.raises(ValueError, match="remaining"):
        ops.perform(initial, "refund-charge", SimpleNamespace(charge_id="charge-1-02", amount_minor=11250, idempotency_key="refund-A"))
    for charge_id, amount in remaining.items():
        args = SimpleNamespace(charge_id=charge_id, amount_minor=amount, idempotency_key=charge_id)
        ops.perform(initial, "refund-charge", args)
        assert ops.perform(initial, "refund-charge", args)["replayed"]
    (tmp_path / "service-state.json").write_text(json.dumps(initial))
    answer = {"refunded_charge_ids": sorted(remaining), "total_refunded_minor": sum(remaining.values())}
    assert tasks.grade("refund_train", answer, tmp_path)["passed"]
    # A correct claim cannot hide an additional, incorrect refund.
    ops.perform(initial, "refund-charge", SimpleNamespace(charge_id="charge-1-01", amount_minor=1, idempotency_key="bad"))
    (tmp_path / "service-state.json").write_text(json.dumps(initial))
    assert not tasks.grade("refund_train", answer, tmp_path)["passed"]


def test_export_requires_ready_artifact_and_exact_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(ops, "HERE", tmp_path)
    initial = copy.deepcopy(tasks.CASES["export_train"]["state"])
    result = ops.perform(initial, "begin-export", SimpleNamespace(customer="customer-101"))
    job_id = result["job_id"]
    with pytest.raises(ValueError, match="artifact_id"):
        ops.perform(initial, "fetch-artifact", SimpleNamespace(artifact_id=job_id, output="invoices.csv"))
    assert ops.perform(initial, "export-status", SimpleNamespace(job_id=job_id))["state"] == "running"
    ready = ops.perform(initial, "export-status", SimpleNamespace(job_id=job_id))
    downloaded = ops.perform(initial, "fetch-artifact", SimpleNamespace(artifact_id=ready["artifact_id"], output="invoices.csv"))
    assert tasks.grade("export_train", downloaded, tmp_path)["passed"]
    (tmp_path / "invoices.csv").write_text("invoice_id,customer_id,amount_minor,status\n")
    assert not tasks.grade("export_train", downloaded, tmp_path)["passed"]


@pytest.mark.parametrize("case", ledger_tasks.CASES)
def test_ledger_key_requires_revisions_utc_refund_sign_and_currency_separation(case, tmp_path):
    from datetime import UTC, datetime

    spec = ledger_tasks.CASES[case]
    latest = {}
    for row in spec["state"]["ledger"]:
        if row["revision"] > latest.get(row["event_id"], {}).get("revision", -1):
            latest[row["event_id"]] = row
    target = 3 + {"train": 1, "a": 2, "b": 3, "c": 4, "d": 5, "e": 6, "f": 7}[spec["variant"]]
    cid = next(c["id"] for c in spec["state"]["customers"] if c["environment"] == "production")
    kept = [
        row
        for row in latest.values()
        if row["customer_id"] == cid
        and row["status"] == "settled"
        and datetime.fromisoformat(row["occurred_at"].replace("Z", "+00:00")).astimezone(UTC).month == target
    ]
    totals = {}
    for row in kept:
        sign = 1 if row["kind"] == "capture" else -1
        totals[row["currency"]] = totals.get(row["currency"], 0) + sign * row["amount_minor"]
    answer = {"totals_minor": totals, "included_event_ids": sorted(row["event_id"] for row in kept)}
    assert answer == spec["expected"]
    (tmp_path / "report.json").write_text(json.dumps(answer))
    assert tasks.grade(case, answer, tmp_path)["passed"]
    bad = copy.deepcopy(answer)
    bad["totals_minor"]["USD"] += 1
    (tmp_path / "report.json").write_text(json.dumps(bad))
    assert not tasks.grade(case, answer, tmp_path)["passed"]
    (tmp_path / "report.json").write_text(json.dumps(answer))
    bad["totals_minor"] = answer["totals_minor"]
    bad["included_event_ids"] = bad["included_event_ids"][:-1]
    assert not tasks.grade(case, bad, tmp_path)["passed"]
