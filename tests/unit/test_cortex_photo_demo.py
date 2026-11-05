"""Demo invariants: credential isolation, source selection, and evidence grading."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit
DEMO = Path(__file__).resolve().parents[2] / "demos" / "cortex-photo"


@pytest.fixture
def bundle(monkeypatch, tmp_path):
    modules = {}
    for name in ("services", "report", "demo"):
        spec = importlib.util.spec_from_file_location(name, DEMO / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[name] = module
        monkeypatch.setattr(module, "RESULTS", tmp_path, raising=False)
        monkeypatch.setattr(module, "RUNTIME", tmp_path / "runtime", raising=False)
        monkeypatch.setattr(module, "STATE", tmp_path / "state.json", raising=False)
    return SimpleNamespace(**modules, root=tmp_path)


def state_for(bundle, guided_correct=True):
    state = {
        "created_at": "2026-11-04T00:00:00Z",
        "model": "task-model",
        "learning_model": "reflection-model",
        "sessions": {
            "baseline": {"id": "baseline-id", "prompt": "same prompt", "captured": True, "first_request_evolve": {"injected": False}},
            "training": {"id": "training-id", "prompt": "GPS task", "captured": True},
            "guided": {
                "id": "guided-id",
                "prompt": "same prompt",
                "captured": True,
                "first_request_evolve": {"injected": True, "ids": ["served-id"]},
            },
        },
        "promoted": [
            {
                "candidate_id": "candidate-id",
                "id": "served-id",
                "content": "Use numeric output <script>alert(1)</script>",
                "metadata": {"session_id": "training-id"},
            }
        ],
    }
    for arm in ("baseline", "guided"):
        output = bundle.root / "photos" / arm
        output.mkdir(parents=True)
        data = {"FocalLength": 4.38 if arm == "guided" and guided_correct else "4.4 mm", "FocalLengthIn35mmFormat": 27}
        (output / "photo-result.json").write_text(json.dumps([data]))
        (bundle.root / f"{arm}-cortex-session.json").write_text(
            json.dumps(
                {
                    "events": [
                        {
                            "seq": 1,
                            "phase": "request",
                            "inference": {"agentRole": "main", "tools": [{"name": "Bash"}]},
                            "plugins": {"evolve": state["sessions"][arm]["first_request_evolve"]},
                        }
                    ]
                }
            )
        )
    return state


def test_credentials_override_inherited_empty_keys_and_proxies(bundle, monkeypatch):
    credentials = bundle.root / "credentials.env"
    credentials.write_text(
        "ANTHROPIC_BASE_URL=https://gateway.example/v1\nANTHROPIC_API_KEY=demo-key\nANTHROPIC_MODEL=task\nOPENAI_BASE_URL=https://gateway.example/v1\nOPENAI_API_KEY=reflection-key\nEVOLVE_MODEL_NAME=reflect\n"
    )
    monkeypatch.setenv("DEMO_CREDENTIALS", str(credentials))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "inherited-token")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "inherited-oauth")
    monkeypatch.setenv("HTTPS_PROXY", "http://old-proxy")
    env = bundle.services.environment()
    assert env["ANTHROPIC_API_KEY"] == "demo-key"
    assert env["OPENAI_API_KEY"] == "reflection-key"
    assert env["ANTHROPIC_BASE_URL"] == "https://gateway.example"
    assert not any(name in env for name in ("ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "HTTPS_PROXY"))


def test_anthropic_reflection_shares_connection_without_openai_credentials(bundle, monkeypatch):
    credentials = bundle.root / "credentials.env"
    credentials.write_text(
        "ANTHROPIC_BASE_URL=https://anthropic.example/v1\nANTHROPIC_API_KEY=demo-key\nANTHROPIC_MODEL=claude-sonnet\nEVOLVE_CUSTOM_LLM_PROVIDER=anthropic\nEVOLVE_MODEL_NAME=claude-opus\n"
    )
    monkeypatch.setenv("DEMO_CREDENTIALS", str(credentials))
    monkeypatch.setenv("ANTHROPIC_API_BASE", "https://inherited.example")
    monkeypatch.setenv("OPENAI_API_KEY", "inherited-key")
    env = bundle.services.environment()
    assert env["EVOLVE_CUSTOM_LLM_PROVIDER"] == "anthropic"
    assert env["EVOLVE_GUIDELINES_MODEL"] == "claude-opus"
    assert env["ANTHROPIC_API_KEY"] == "demo-key"
    assert env["ANTHROPIC_API_BASE"] == "https://anthropic.example"
    assert "OPENAI_API_KEY" not in env
    task_env = bundle.demo.claude_environment(bundle.root / "config")
    assert task_env["ANTHROPIC_BASE_URL"] == bundle.services.BRIDGE
    assert task_env["ANTHROPIC_API_BASE"] == "https://anthropic.example"


def test_openai_reflection_requires_its_own_connection(bundle, monkeypatch):
    credentials = bundle.root / "credentials.env"
    credentials.write_text(
        "ANTHROPIC_BASE_URL=https://anthropic.example\nANTHROPIC_API_KEY=demo-key\nANTHROPIC_MODEL=claude-sonnet\nEVOLVE_CUSTOM_LLM_PROVIDER=openai\nEVOLVE_MODEL_NAME=claude-opus\n"
    )
    monkeypatch.setenv("DEMO_CREDENTIALS", str(credentials))
    with pytest.raises(RuntimeError, match="OPENAI_BASE_URL, OPENAI_API_KEY"):
        bundle.services.environment()


def test_reflection_check_requires_the_structured_output_used_by_learning(bundle, monkeypatch):
    requests = []
    call = SimpleNamespace(function=SimpleNamespace(name="evolve_ready", arguments='{"ready": true}'))

    def completion(**kwargs):
        requests.append(kwargs)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]))])

    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=completion))
    bundle.demo.check_reflection({"EVOLVE_MODEL_NAME": "claude-sonnet", "EVOLVE_CUSTOM_LLM_PROVIDER": "anthropic"})
    assert requests[0]["model"] == "claude-sonnet"
    assert requests[0]["custom_llm_provider"] == "anthropic"
    assert requests[0]["tool_choice"]["function"]["name"] == "evolve_ready"


def test_reflection_check_rejects_a_text_only_success(bundle, monkeypatch):
    answer = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="EVOLVE_READY", tool_calls=[]))])
    monkeypatch.setitem(sys.modules, "litellm", SimpleNamespace(completion=lambda **kwargs: answer))
    with pytest.raises(RuntimeError, match="expected structured tool call"):
        bundle.demo.check_reflection({"EVOLVE_MODEL_NAME": "claude-sonnet", "EVOLVE_CUSTOM_LLM_PROVIDER": "anthropic"})


def test_bridge_preserves_all_system_text_and_tools(bundle):
    body = {
        "system": [{"type": "text", "text": "original constraints"}, {"type": "text", "text": "learned guideline"}],
        "tools": [{"name": "Bash"}],
        "messages": [{"role": "user", "content": "task"}],
        "stream": True,
    }
    forwarded = bundle.services.flatten_system(body)
    assert forwarded["system"] == "original constraints\n\nlearned guideline"
    for name in ("tools", "messages", "stream"):
        assert forwarded[name] == body[name]
    with pytest.raises(ValueError):
        bundle.services.flatten_system({"system": [{"type": "image"}]})


def test_shared_runtime_can_isolate_another_task_family(bundle, monkeypatch):
    config = bundle.services.cortex_config()
    assert config["pipeline"]["outbound"]["plugins"][-1]["config"]["serve_namespace"] == "photo-reviewed"
    monkeypatch.setattr(bundle.services, "SERVE_NAMESPACE", "invoice-reviewed")
    monkeypatch.setattr(bundle.services, "LEARN_NAMESPACE", "invoice-candidates")
    monkeypatch.setattr(bundle.services, "PROCESSING_PROFILE", "invoice-demo")
    config = bundle.services.cortex_config(True)["pipeline"]["outbound"]["plugins"][-1]["config"]
    assert config["serve_namespace"] == "invoice-reviewed"
    assert config["learn_namespace"] == "invoice-candidates"
    assert config["processing_profile"] == "invoice-demo"
    assert config["inject"] is True and config["learn"] is False


@pytest.mark.parametrize("mode", [None, "standard", "consistency"])
def test_shared_runtime_selects_explicit_learning_mode(bundle, monkeypatch, mode):
    credentials = bundle.root / "credentials.env"
    credentials.write_text(
        "ANTHROPIC_BASE_URL=https://gateway.example\nANTHROPIC_API_KEY=demo-key\nANTHROPIC_MODEL=task\nEVOLVE_MODEL_NAME=reflect\nEVOLVE_CUSTOM_LLM_PROVIDER=anthropic\n"
    )
    monkeypatch.setenv("DEMO_CREDENTIALS", str(credentials))
    monkeypatch.setenv("EVOLVE_GUIDELINES_MODE", "all")
    if mode is None:
        monkeypatch.delenv("DEMO_GUIDELINES_MODE", raising=False)
    else:
        monkeypatch.setenv("DEMO_GUIDELINES_MODE", mode)
    assert bundle.services.environment()["EVOLVE_GUIDELINES_MODE"] == (mode or "consistency")


@pytest.mark.parametrize(
    "data,passed",
    [
        ([{"FocalLength": 4.38, "FocalLengthIn35mmFormat": 27}], True),
        ({"FocalLength": 4.38, "FocalLengthIn35mmFilm": 27}, True),
        ({"FocalLength": "4.4 mm", "FocalLengthIn35mmFormat": "27 mm"}, False),
        ({"FocalLength": 4.4, "FocalLengthIn35mmFormat": 27}, False),
        ({"FocalLength": True, "FocalLengthIn35mmFormat": 27}, False),
    ],
)
def test_numeric_checker_uses_precision_and_native_tags(bundle, data, passed):
    path = bundle.root / "metadata.json"
    path.write_text(json.dumps(data))
    assert bundle.report.metadata_check(path)["numeric_correct"] is passed


def test_report_requires_actual_injection_and_escapes_guidelines(bundle):
    state = state_for(bundle)
    summary = bundle.report.build_report(bundle.root, state)
    assert "improved" in summary["verdict"]
    report = (bundle.root / "comparison.html").read_text()
    assert "<script>alert(1)</script>" not in report
    assert "&lt;script&gt;" in report
    path = bundle.root / "guided-cortex-session.json"
    evidence = json.loads(path.read_text())
    evidence["events"][0]["plugins"]["evolve"]["ids"] = ["wrong-id"]
    path.write_text(json.dumps(evidence))
    assert "does not establish" in bundle.report.build_report(bundle.root, state)["verdict"]
    evidence["events"][0]["plugins"]["evolve"]["ids"] = ["served-id"]
    path.write_text(json.dumps(evidence))
    state["sessions"]["guided"]["prompt"] = "changed prompt"
    assert "does not establish" in bundle.report.build_report(bundle.root, state)["verdict"]


def test_report_records_a_tie_instead_of_claiming_improvement(bundle):
    state = state_for(bundle)
    (bundle.root / "photos/baseline/photo-result.json").write_text('{"FocalLength":4.38,"FocalLengthIn35mmFormat":27}')
    assert "no correctness gain" in bundle.report.build_report(bundle.root, state)["verdict"]


def test_report_discloses_reused_recordings_and_learning_workarounds(bundle):
    state = state_for(bundle)
    state.update(
        reused_recordings_from="previous-run",
        learning_provider="anthropic",
        learning_output_mode="JSON with local validation",
        reflection_attempts=[{"status": "failed", "error": "<schema error>"}],
    )
    bundle.report.build_report(bundle.root, state)
    report = (bundle.root / "comparison.html").read_text()
    assert "reused unchanged from previous-run" in report
    assert "Reflection API format: anthropic" in report
    assert "JSON with local validation" in report
    assert "&lt;schema error&gt;" in report
    assert "<schema error>" not in report


def test_report_includes_unpromoted_guidelines_and_run_interpretation(bundle):
    state = state_for(bundle)
    state.update(run_notes=["No benefit from <extra advice>."], review={"reviewer": "Codex", "selected_candidate_ids": ["candidate-id"]})
    (bundle.root / "photo-candidates.json").write_text(
        json.dumps(
            [
                {"id": "candidate-id", "content": "Selected candidate", "metadata": {}},
                {"id": "other-id", "content": "Unpromoted <advice>", "metadata": {"status": "candidate"}},
            ]
        )
    )
    bundle.report.build_report(bundle.root, state)
    report = (bundle.root / "comparison.html").read_text()
    assert "Candidate other-id · not promoted" in report
    assert "Unpromoted &lt;advice&gt;" in report
    assert "No benefit from &lt;extra advice&gt;." in report
    assert "Review decision made before the guided run" in report
    assert "Selected candidate" not in report  # Its reviewed copy is already shown.


def test_attribution_uses_tool_manifest_when_request_has_no_tool_count(bundle):
    event = {
        "seq": 3,
        "phase": "request",
        "inference": {"agentRole": "main", "tools": [{"name": "Bash"}]},
        "plugins": {"evolve": {"injected": True, "ids": ["reviewed"]}},
    }
    side = {"seq": 1, "phase": "request", "inference": {"agentRole": "main"}}
    assert bundle.report.first_request_attribution({"events": [side, event]}) == event["plugins"]["evolve"]


def test_learning_processes_only_the_selected_training_capture(bundle, monkeypatch):
    state = {"sessions": {"baseline": {"id": "do-not-learn"}, "training": {"id": "selected-training", "captured": True}}}
    bundle.services.save_state(state)
    trajectory = {"batch": {"conversation_id": "selected-training"}, "messages": [{"role": "user", "content": "verified GPS procedure"}]}
    (bundle.root / "training-trajectory.json").write_text(json.dumps({"trajectories": [trajectory]}))
    calls = []
    client = SimpleNamespace(
        ensure_namespace=lambda _: None,
        process_trajectory=lambda t, **kw: calls.append((t, kw)) or SimpleNamespace(model_dump=lambda **_: {"updates": []}),
    )
    monkeypatch.setattr("altk_evolve.frontend.client.evolve_client.EvolveClient", lambda: client)
    monkeypatch.setattr(bundle.demo, "candidates", lambda: [])
    bundle.demo.learn()
    assert calls == [(trajectory, {"namespace_id": "photo-candidates"})]
    assert bundle.services.load_state()["learned"] is True
    bundle.demo.learn()
    assert len(calls) == 1


def test_learning_rejects_a_mismatched_source_before_processing(bundle):
    bundle.services.save_state({"sessions": {"training": {"id": "selected-training", "captured": True}}})
    (bundle.root / "training-trajectory.json").write_text(json.dumps({"trajectories": [{"batch": {"conversation_id": "baseline-id"}}]}))
    with pytest.raises(RuntimeError, match="does not match"):
        bundle.demo.learn()


def test_interactive_arms_are_fresh_and_have_the_same_prompt(bundle):
    bundle.services.save_state({"model": "task-model"})
    command = bundle.demo.claude_command("fresh-id")
    assert "-p" not in command and "--dangerously-skip-permissions" not in command
    assert "--resume" not in command
    assert bundle.demo.prompt("baseline") == bundle.demo.prompt("guided")
