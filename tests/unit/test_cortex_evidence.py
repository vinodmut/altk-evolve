"""Evidence survives the Cortex adapter and every extraction prompt without replay mutation."""

import copy
import json
from types import SimpleNamespace

import pytest

from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.llm.guidelines import consistency_guidelines, guidelines
from altk_evolve.llm.guidelines.context import bounded_text, render_input_evidence
from altk_evolve.processing import ProcessorContext, Trajectory, TrajectoryBatch
from altk_evolve.processing.builtin import GuidelineConfig, GuidelineProcessor

pytestmark = pytest.mark.unit


@pytest.fixture
def prompts(monkeypatch):
    captured = []

    def complete(**kwargs):
        captured.append(kwargs["messages"][0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"guidelines": []}'))])

    for module in (guidelines, consistency_guidelines):
        monkeypatch.setattr(module, "completion", complete)
        monkeypatch.setattr(module, "get_supported_openai_params", lambda **kwargs: [])
        monkeypatch.setattr(module, "supports_response_schema", lambda **kwargs: False)
    return captured


@pytest.mark.parametrize("method", ["standard", "fast", "accurate"])
def test_user_feedback_rules_and_boundary_tool_output_reach_prompts(method, prompts):
    context = [
        {"role": "user", "content": "Reconcile revenue"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "prior", "type": "function", "function": {"name": "ledger_export", "arguments": '{"account":"A"}'}}],
        },
        {"role": "system", "content": "Available tool definitions: ledger_export requires an account, never customer_id"},
    ]
    messages = [
        {"role": "tool", "tool_call_id": "prior", "content": "[error] ledger_export refused customer_id"},
        {"role": "system", "content": "SYSTEM_RULE: use uv for Python"},
        {"role": "assistant", "content": "Gross revenue matches"},
        {"role": "user", "content": "CORRECTION: use net revenue, not gross"},
        {"role": "tool", "tool_call_id": "unavailable", "content": "UNPAIRED_OBSERVATION: artifact is invalid"},
    ]
    before = copy.deepcopy((context, messages))
    options = GuidelineRuntime()
    if method == "standard":
        guidelines.generate_guidelines(messages, context_messages=context, options=options)
    elif method == "fast":
        consistency_guidelines.generate_consistency_guidelines_fast({"messages": messages, "context_messages": context}, options=options)
    else:
        consistency_guidelines._generate_guideline_result(
            task_description="Reconcile revenue",
            messages=messages,
            consistency_data={"step_uncertainties": {1: 0.5}},
            constrained_decoding_supported=False,
            step_range=None,
            debug_suffix="",
            options=options,
            context_messages=context,
        )
    [prompt] = prompts
    for marker in ("SYSTEM_RULE", "CORRECTION: use net", "ledger_export refused", "UNPAIRED_OBSERVATION", "requires an account"):
        assert marker in prompt
    assert "action unavailable in captured context" in prompt
    assert (context, messages) == before


def test_tool_schema_and_description_reach_standard_prompt(prompts):
    tools = [
        {
            "type": "function",
            "function": {
                "name": "export",
                "description": "Export after account resolution",
                "parameters": {"type": "object", "required": ["account", "output"]},
            },
        }
    ]
    guidelines.generate_guidelines(
        [{"role": "user", "content": "Export the ledger"}, {"role": "assistant", "content": "Done"}],
        tools=tools,
        options=GuidelineRuntime(),
    )
    assert "Export after account resolution" in prompts[0] and '"required": ["account", "output"]' in prompts[0]


def test_reported_usage_and_status_reach_the_learning_prompt(prompts):
    trajectory = Trajectory(
        messages=[{"role": "user", "content": "Reconcile revenue"}, {"role": "assistant", "content": "Trying again"}],
        metadata={
            "cortex_capture": {"turns": [{"seq": 4, "durationMs": 321, "inference": {"totalTokens": 1234, "finishReason": "max_tokens"}}]}
        },
    )
    GuidelineProcessor.from_config(GuidelineConfig()).process(trajectory, context=ProcessorContext(operation_id="test"))
    assert '"totalTokens": 1234' in prompts[0] and '"durationMs": 321' in prompts[0]
    assert "not a ground-truth task evaluation" in prompts[0]


def test_sdk_tool_result_is_not_mislabelled_as_unpaired():
    messages = [
        {"role": "assistant", "content": [{"type": "function_call", "id": "sdk", "function": {"name": "export", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "sdk", "content": "valid artifact"},
    ]
    assert render_input_evidence(messages) == ""


def test_observer_annotations_enrich_extraction_without_entering_replay():
    observation = {"role": "system", "cortex_observation": True, "content": "CHILD_INTERNAL_FINDING"}
    messages = [
        {"role": "user", "content": "Task"},
        {"role": "assistant", "content": "First"},
        observation,
        {"role": "assistant", "content": "Second"},
    ]
    assert "CHILD_INTERNAL_FINDING" in render_input_evidence(messages)
    replay = consistency_guidelines.transform_trajectory_to_IR({"messages": messages, "context_messages": [observation]})
    assert len(replay["steps"]) == 2
    assert "CHILD_INTERNAL_FINDING" not in json.dumps(replay)


def test_long_observed_output_keeps_failure_at_end(prompts):
    output = "START_OF_LOG\n" + "x" * 12000 + "\nFINAL_CHECK_FAILED: report total does not reconcile"
    messages = [
        {"role": "user", "content": "Validate report"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "check", "type": "function", "function": {"name": "Bash", "arguments": '{"command":"validate"}'}}],
        },
        {"role": "tool", "tool_call_id": "check", "content": output},
    ]
    guidelines.generate_guidelines(messages, options=GuidelineRuntime(trajectory_max_step_chars=300))
    assert "START_OF_LOG" in prompts[0] and "FINAL_CHECK_FAILED" in prompts[0]
    assert "middle omitted" in prompts[0]
    assert messages[-1]["content"] == output


def test_cortex_default_prompt_preserves_outcome_tail(prompts):
    trajectory = Trajectory(
        messages=[{"role": "user", "content": "Do the task"}]
        + [{"role": "assistant", "content": f"Action {i}"} for i in range(60)]
        + [{"role": "assistant", "content": "FINAL_RECOVERY: corrected the report"}],
        batch=TrajectoryBatch(source="cortex", conversation_id="session", batch_id="2-122"),
    )
    processor = GuidelineProcessor.from_config(GuidelineConfig(trajectory_max_steps=50, trajectory_tail_steps=0))
    processor.process(trajectory, context=ProcessorContext(operation_id="test"))
    assert "Action 0" in prompts[0] and "FINAL_RECOVERY" in prompts[0] and "intermediate step(s) omitted" in prompts[0]


@pytest.mark.parametrize("budget", [1, 3, 20, 100, 20000])
def test_evidence_budgets_are_bounded_and_explicit(budget):
    text = "start " + "x" * 50000 + " tail"
    rendered = bounded_text(text, budget)
    assert len(rendered) <= budget
    if budget >= 100:
        assert "start" in rendered and "tail" in rendered and "middle omitted" in rendered
    evidence = render_input_evidence([{"role": "system", "content": text}], max_chars=budget)
    assert len(evidence) <= budget


def test_evidence_records_relative_order_without_adding_agent_steps():
    messages = [
        {"role": "user", "content": "Task"},
        {"role": "assistant", "content": "Before"},
        {"role": "user", "content": "Correct the calculation"},
        {"role": "assistant", "content": "After"},
    ]
    evidence = render_input_evidence(messages)
    assert "Message 3 - user input" in evidence
    data = guidelines.parse_openai_agents_trajectory(messages)
    assert data["num_steps"] == 2
    assert data["trajectory_summary"] == "\n\n".join(data["steps_list"])
    assert json.dumps(messages).count("Correct the calculation") == 1
