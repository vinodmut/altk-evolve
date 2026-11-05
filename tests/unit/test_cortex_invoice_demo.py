"""Invoice grading: exact money, complete descriptions, duplicates and isolation."""

import csv
import copy
import gzip
import hashlib
import importlib.util
import json
import subprocess
import sys
import types
from decimal import Decimal
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2] / "demos/cortex-invoices"
SPEC = importlib.util.spec_from_file_location("invoice_evaluate", ROOT / "evaluate.py")
EVALUATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVALUATOR)
ORACLE = json.loads((ROOT / "oracle/reference.json").read_text())


def write_outputs(root, invoices):
    items = [
        {field: invoice[field] if field in ("invoice_id", "vendor") else row[field] for field in EVALUATOR.ITEM_FIELDS}
        for invoice in invoices
        for row in invoice["rows"]
    ]
    recon = [{field: invoice[field] for field in EVALUATOR.RECON_FIELDS} for invoice in invoices]
    for name, fields, rows in (("items.csv", EVALUATOR.ITEM_FIELDS, items), ("reconciliation.csv", EVALUATOR.RECON_FIELDS, recon)):
        with (root / name).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
    return items, recon


def test_complete_reference_packet_passes(tmp_path):
    write_outputs(tmp_path, ORACLE["training"])
    score = EVALUATOR.evaluate(tmp_path, ORACLE["training"])
    assert score["all_correct"]
    assert score["items"]["exact_rows"] == 16
    assert score["reconciliation"]["exact_rows"] == 3


def test_duplicate_correct_row_fails_and_reduces_accuracy(tmp_path):
    items, _ = write_outputs(tmp_path, ORACLE["training"])
    with (tmp_path / "items.csv").open("a", newline="") as stream:
        csv.DictWriter(stream, fieldnames=EVALUATOR.ITEM_FIELDS).writerow(items[0])
    score = EVALUATOR.evaluate(tmp_path, ORACLE["training"])
    assert not score["all_correct"]
    assert score["items"]["row_accuracy"] == 16 / 17


@pytest.mark.parametrize("field,wrong", [("invoice_id", "1071"), ("description", "Archive cartons"), ("line_amount", "99.61")])
def test_id_description_and_exact_amount_errors_are_detected(tmp_path, field, wrong):
    items, _ = write_outputs(tmp_path, ORACLE["training"])
    items[0][field] = wrong
    with (tmp_path / "items.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=EVALUATOR.ITEM_FIELDS)
        writer.writeheader()
        writer.writerows(items)
    score = EVALUATOR.evaluate(tmp_path, ORACLE["training"])
    assert not score["all_correct"]
    assert score["items"]["exact_rows"] == 15


def test_unescaped_description_comma_is_a_format_error(tmp_path):
    write_outputs(tmp_path, ORACLE["training"])
    with (tmp_path / "items.csv").open("a") as stream:
        stream.write("001071,Alder Office Supply,Extra, comma,1,2.00,2.00\n")
    assert EVALUATOR.evaluate(tmp_path, ORACLE["training"])["format_errors"]


def test_missing_outputs_are_recorded_failures(tmp_path):
    score = EVALUATOR.evaluate(tmp_path, ORACLE["training"])
    assert not score["all_correct"]
    assert len(score["format_errors"]) == 2


@pytest.mark.parametrize("value", ["NaN", "Infinity", "", "one"])
def test_nonfinite_and_invalid_numbers_fail(value):
    assert EVALUATOR.normalized("line_amount", value) == "INVALID_NUMBER"


def test_numeric_formatting_is_not_an_unstated_requirement():
    assert EVALUATOR.normalized("line_amount", "($1,245.60)") == Decimal("-1245.60")
    assert EVALUATOR.normalized("unit_price", "4.3750") == Decimal("4.375")
    assert EVALUATOR.normalized("invoice_id", "001071") == "001071"


def test_fixtures_have_disjoint_ids_and_consistent_references():
    seen = set()
    manifest = json.loads((ROOT / "fixture-manifest.json").read_text())
    assert len(manifest) == 12
    for invoices in ORACLE.values():
        ids = {invoice["invoice_id"] for invoice in invoices}
        assert not ids & seen
        seen |= ids
        assert sum(len(invoice["rows"]) for invoice in invoices) == 16
        for invoice in invoices:
            subtotal = sum(Decimal(row["line_amount"]) for row in invoice["rows"])
            computed = subtotal + Decimal(invoice["tax"])
            assert Decimal(invoice["subtotal"]) == subtotal
            assert Decimal(invoice["computed_total"]) == computed
            assert Decimal(invoice["difference"]) == computed - Decimal(invoice["printed_total"])
    for file, metadata in manifest.items():
        assert hashlib.sha256((ROOT / file).read_bytes()).hexdigest() == metadata["sha256"]
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "COPY --chown=demo:demo fixtures/ fixtures/" in dockerfile
    assert "oracle" not in dockerfile


def test_distribution_preserves_failures_and_run_variation():
    rows = [
        {"all_correct": True, "tool_calls": 16, "observed_tool_errors": 3},
        {"all_correct": False, "tool_calls": 22, "observed_tool_errors": 5},
        {"all_correct": True, "tool_calls": 17, "observed_tool_errors": 2},
    ]
    result = EVALUATOR.distribution(rows)
    assert result["correct_packets"] == 2
    assert result["tool_calls"] == [16, 22, 17]
    assert result["median_tool_calls"] == 17
    assert (result["min_tool_calls"], result["max_tool_calls"]) == (16, 22)


def test_compressed_event_archive_preserves_json(tmp_path):
    path = tmp_path / "session.json"
    expected = {"events": [{"seq": 1, "description": "Continuation; 2 m", "amount": "4.375"}]}
    compressed = path.with_suffix(".json.gz")
    compressed.write_bytes(gzip.compress(json.dumps(expected).encode(), mtime=0))
    assert EVALUATOR.load(path) == expected
    assert EVALUATOR.artifact_name(tmp_path, path.name) == "session.json.gz"


@pytest.mark.parametrize("transition", [False, True])
def test_changed_harness_is_excluded_despite_valid_inputs_and_injection(monkeypatch, transition):
    sessions = {arm: {"prompt": "same task", "input_hashes_before": {"invoice.pdf": "same hash"}} for arm in ("baseline", "guided")}

    def measured(results, state, label, oracle):
        return {
            "inputs_unchanged": True,
            "harness": {"mode": "noninteractive"},
            "harness_transition": transition and label == "baseline",
            "first_request_evolve": {"injected": True, "ids": ["1"]} if label == "guided" else {},
        }

    monkeypatch.setattr(EVALUATOR, "metrics", measured)
    result = EVALUATOR.comparison(None, {"sessions": sessions}, None, {"1"}, {arm: arm for arm in sessions})
    assert result["input_and_injection_valid"]
    assert result["valid_comparison"] is not transition


def test_batch_repetition_is_fresh_preserves_evidence_and_uses_no_stdin(tmp_path, monkeypatch):
    original = tmp_path / "results/tasks/baseline-1"
    original.mkdir(parents=True)
    (original / "items.csv").write_text("original output")
    state = {
        "checked_at": "already checked",
        "sessions": {"baseline-1": {"id": "original-uuid", "captured": True, "exit_code": 0}},
    }
    services = types.ModuleType("services")
    services.RESULTS = tmp_path / "results"
    services.RUNTIME = tmp_path / "runtime"
    services.health = lambda: None
    services.load_state = lambda: copy.deepcopy(state)

    def save(value):
        state.clear()
        state.update(copy.deepcopy(value))

    services.save_state = save
    injections = []
    services.set_injection = injections.append
    shared = types.ModuleType("demo")
    shared.claude_command = lambda session=None: ["claude", "--session-id", session] if session else ["claude"]
    shared.claude_environment = lambda config: {}
    shared.prepare_config = lambda config: config.mkdir(parents=True)
    report = types.ModuleType("report")
    report.first_request_attribution = lambda evidence: {}
    for name, module in (("services", services), ("demo", shared), ("report", report)):
        monkeypatch.setitem(sys.modules, name, module)
    spec = importlib.util.spec_from_file_location("invoice_test_driver", ROOT / "driver.py")
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    captured = []
    monkeypatch.setattr(driver, "capture", captured.append)
    commands = []

    def run(command, **kwargs):
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["cwd"] != original
        assert "--dangerously-skip-permissions" not in command
        assert "dontAsk" in command and "none" in command
        kwargs["stdout"].write('{"is_error": false, "permission_denials": []}')
        commands.append(command)
        return types.SimpleNamespace(returncode=0)

    monkeypatch.setattr(driver.subprocess, "run", run)
    driver.task("baseline", 1, batch=True, repeat=2)
    new = state["sessions"]["baseline-1-repeat-2"]
    assert new["id"] != "original-uuid"
    assert new["harness"]["mode"] == "noninteractive"
    assert new["input_hashes_before"] == driver.hashes(ROOT / "fixtures/trial-1")
    assert state["repetition_plan"]["repetitions"] == [1, 2]
    assert injections == [False] and captured == ["baseline-1-repeat-2"]
    assert (original / "items.csv").read_text() == "original output"
    assert not (services.RESULTS / "tasks/baseline-1-repeat-2/items.csv").exists()
    assert commands[0][-1] == driver.prompt()
    with pytest.raises(RuntimeError, match="already exists"):
        driver.task("baseline", 1, batch=True, repeat=2)
    assert len(commands) == 1
    with pytest.raises(RuntimeError, match="Completed evaluation sessions are frozen"):
        driver.task("baseline", 1, resume=True, batch=True)
