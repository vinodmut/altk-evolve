"""Repository-quirk demo: the traps fire, the grader is strict, and trap counts read tool output."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2] / "demos/cortex-repo"
TASK = {"id": "order-discount", "model": "Order", "field": "discount_code", "type": "str", "value": "SPRING10"}


def module(name):
    sys.path.insert(0, str(ROOT))
    try:
        spec = importlib.util.spec_from_file_location(f"repo_{name}", ROOT / f"{name}.py")
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        return loaded
    finally:
        sys.path.remove(str(ROOT))


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args], cwd=repo, capture_output=True, text=True, check=check
    )


@pytest.fixture
def repo(tmp_path, monkeypatch):
    target = tmp_path / "repo"
    shutil.copytree(ROOT / "shopkit", target, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # The repository's tools call python3; point it at this interpreter and its packages.
    wrapper = bin_dir / "python3"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}:{subprocess.os.environ['PATH']}")
    git(target, "init", "-q")
    git(target, "config", "core.hooksPath", ".githooks")
    git(target, "add", "-A")
    git(target, "commit", "-q", "-m", "chore: init")
    return target


def add_field(repo):
    path = repo / "schema" / "models.yaml"
    path.write_text(
        path.read_text().replace("      total_cents: {type: int}\n", "      total_cents: {type: int}\n      discount_code: {type: str}\n")
    )
    subprocess.run([sys.executable, "tools/generate.py"], cwd=repo, check=True, capture_output=True)


def test_shipped_generated_code_matches_the_schema(repo):
    assert subprocess.run([sys.executable, "tools/check.py"], cwd=repo, capture_output=True).returncode == 0


def test_plain_pytest_fails_but_the_runner_passes(repo):
    plain = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], cwd=repo, capture_output=True, text=True)
    assert plain.returncode != 0 and "SHOPKIT_DB is not set" in plain.stdout
    assert subprocess.run(["./scripts/test", "-p", "no:cacheprovider"], cwd=repo, capture_output=True).returncode == 0


def test_hook_rejects_a_schema_change_without_migration_and_changelog(repo):
    add_field(repo)
    git(repo, "add", "-A")
    rejected = git(repo, "commit", "-q", "-m", "add discount", check=False)
    assert rejected.returncode != 0
    output = rejected.stdout + rejected.stderr
    assert "migrations do not create orders.discount_code" in output and "without an entry in CHANGES.md" in output


def test_grader_passes_only_a_complete_committed_change(repo):
    grade = module("grade")
    add_field(repo)
    assert not grade.grade(repo, TASK)["passed"]
    subprocess.run([sys.executable, "tools/migrate.py", "new", "order_discount_code"], cwd=repo, check=True, capture_output=True)
    changes = repo / "CHANGES.md"
    changes.write_text(changes.read_text().replace("## Unreleased\n", "## Unreleased\n\n- Orders have an optional `discount_code`.\n"))
    assert not grade.grade(repo, TASK)["checks"]["committed"]
    git(repo, "add", "-A")
    rejected = git(repo, "commit", "-q", "-m", "Add discount code", check=False)
    assert rejected.returncode != 0 and "bump clients/python/VERSION" in rejected.stdout + rejected.stderr
    (repo / "clients/python/VERSION").write_text("1.4.0\n")
    git(repo, "add", "-A")
    rejected = git(repo, "commit", "-q", "-m", "Add discount code", check=False)
    assert rejected.returncode != 0 and "Conventional Commit" in rejected.stdout + rejected.stderr
    git(repo, "commit", "-q", "-m", "feat(orders): add discount code")
    verdict = grade.grade(repo, TASK)
    assert verdict["passed"], verdict


def test_trap_hits_come_from_tool_output():
    summary = module("summary")
    rows = [
        {"role": "assistant", "tool_calls": [{"id": "a", "function": {"name": "Bash", "arguments": json.dumps({"command": "pytest"})}}]},
        {"role": "tool", "tool_call_id": "a", "content": "[error] RuntimeError: SHOPKIT_DB is not set"},
        {"role": "assistant", "content": "The hook said migrations do not create orders.x; I will add one."},
        {"role": "tool", "tool_call_id": "b", "content": "[error] check failed: migrations do not create orders.discount_code"},
    ]
    hits = summary.trap_hits(rows)
    assert hits["plain_pytest"] == 1 and hits["missing_migration"] == 1 and hits["missing_changelog"] == 0
    assert hits["client_version"] == 0 and hits["commit_message"] == 0
    read_hook = [{"role": "tool", "tool_call_id": "c", "content": "echo 'pre-commit: part of the change is not staged'"}]
    assert summary.trap_hits(read_hook)["partial_staging"] == 0 and summary.commit_rejections(read_hook) == 0
    assert summary.commit_rejections(rows) == 1


def test_pairs_resume_finishes_an_interrupted_pair(tmp_path, monkeypatch):
    fake = types_module(tmp_path)
    monkeypatch.setitem(sys.modules, "services", fake)
    monkeypatch.setitem(sys.modules, "demo", type(sys)("demo"))
    monkeypatch.setitem(sys.modules, "report", type(sys)("report"))
    sys.modules["report"].first_request_attribution = lambda evidence: {}
    driver = module("driver")
    fake.state["sessions"] = {
        "training": {"captured": True},
        "baseline-1": {"captured": True},
        "guided-1": {"captured": True},
        "guided-2": {"captured": False, "arm": "guided"},
    }
    (tmp_path / "guided-2-cli.json").write_text("{}")
    launched = []
    monkeypatch.setattr(driver, "retrieval_preview", lambda: _done({"entity_ids": ["1"]}))
    monkeypatch.setattr(driver, "launch", lambda label, task, arm: launched.append(label))
    driver.pairs(1)
    assert launched == ["guided-2", "baseline-2"]
    assert "guided-2-interrupted-1" in fake.state["sessions"] and (tmp_path / "guided-2-interrupted-1-cli.json").exists()


async def _done(value):
    return value


def types_module(root):
    services = type(sys)("services")
    services.RESULTS = root
    services.LEARN_NAMESPACE, services.SERVE_NAMESPACE = "repo-candidates", "repo-reviewed"
    services.state = {"sessions": {}, "promoted": [{"id": "1"}]}
    services.load_state = lambda: services.state
    services.save_state = lambda state: None
    services.write_json = lambda path, value: None
    services.set_injection = lambda enabled: None
    return services
