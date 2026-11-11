"""guidelines_prompt selects the prompt the standard guideline generator renders."""

from types import SimpleNamespace

import pytest

from altk_evolve.config.evolve import EvolveConfig
from altk_evolve.config.guideline_prompts import PROMPTS_DIR, resolve_guidelines_prompt
from altk_evolve.config.guideline_runtime import GuidelineRuntime
from altk_evolve.processing import ProcessingError, ProcessingManager, ProcessorRegistry
from altk_evolve.processing.builtin import GuidelineProcessor

pytestmark = pytest.mark.unit

# A line only the "rules" prompt has.
RULES_MARKER = "# Requirements the environment stated"


def _manager() -> ProcessingManager:
    registry = ProcessorRegistry()
    registry.register(GuidelineProcessor)
    return ProcessingManager(registry=registry)


def _render(monkeypatch, options: GuidelineRuntime) -> str:
    """Run one generation call and return the prompt the model was sent."""
    import altk_evolve.llm.guidelines.guidelines as guidelines

    sent = []

    def completion(*, messages, **kwargs):
        sent.append(messages[0]["content"])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"guidelines": []}'))])

    monkeypatch.setattr(guidelines, "completion", completion)
    monkeypatch.setattr(guidelines, "dispatch_llm_pre_call", lambda messages, **kwargs: messages)
    guidelines._generate_guidelines_for_segment("Add a field", "1. edit a file", 1, True, options=options)
    return sent[0]


def test_the_default_prompt_is_the_one_every_earlier_release_used(monkeypatch):
    monkeypatch.delenv("EVOLVE_GUIDELINES_PROMPT", raising=False)

    assert EvolveConfig(_env_file=None).guidelines_prompt == "default"
    assert GuidelineRuntime().guidelines_prompt == "default"
    assert resolve_guidelines_prompt("default") == PROMPTS_DIR / "generate_guidelines.jinja2"


def test_the_prompt_can_be_chosen_from_the_environment(monkeypatch):
    monkeypatch.setenv("EVOLVE_GUIDELINES_PROMPT", "rules")

    assert EvolveConfig(_env_file=None).guidelines_prompt == "rules"


def test_the_generator_renders_the_configured_prompt(monkeypatch):
    default = _render(monkeypatch, GuidelineRuntime())
    rules = _render(monkeypatch, GuidelineRuntime(guidelines_prompt="rules"))

    assert RULES_MARKER not in default
    assert RULES_MARKER in rules
    # Both are rendered with the same variables.
    assert "Add a field" in default and "Add a field" in rules


def test_a_prompt_file_outside_the_package_can_be_used(monkeypatch, tmp_path):
    custom = tmp_path / "mine.jinja2"
    custom.write_text("Custom prompt for {{task_instruction}}")

    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt=str(custom))) == "Custom prompt for Add a field"
    # An edited file is read again, without a restart.
    custom.write_text("Edited prompt for {{task_instruction}}")
    import os

    os.utime(custom, ns=(custom.stat().st_atime_ns, custom.stat().st_mtime_ns + 1_000_000))
    assert _render(monkeypatch, GuidelineRuntime(guidelines_prompt=str(custom))) == "Edited prompt for Add a field"


@pytest.mark.parametrize("name", ["typo", "", "/no/such/prompt.jinja2"])
def test_an_unknown_prompt_is_rejected_before_anything_is_generated(name):
    with pytest.raises(ValueError, match="guidelines_prompt"):
        GuidelineRuntime(guidelines_prompt=name)


def test_a_profile_can_choose_its_prompt_and_records_it():
    plan = _manager().validate({"processors": [{"id": "g", "plugin": "evolve.guidelines", "config": {"guidelines_prompt": "rules"}}]})

    assert plan.manifest()["processors"][0]["config"]["guidelines_prompt"] == "rules"


def test_a_profile_without_a_prompt_captures_the_environment_default(monkeypatch):
    from altk_evolve.config.evolve import evolve_config

    monkeypatch.setattr(evolve_config, "guidelines_prompt", "rules")
    plan = _manager().validate({"processors": [{"id": "g", "plugin": "evolve.guidelines", "config": {}}]})

    assert plan.manifest()["processors"][0]["config"]["guidelines_prompt"] == "rules"


def test_a_profile_stored_before_the_setting_existed_replays_with_the_default_prompt(monkeypatch):
    from altk_evolve.config.evolve import evolve_config

    manager = _manager()
    legacy = manager.validate({"processors": [{"id": "g", "plugin": "evolve.guidelines", "config": {}}]}).manifest()
    del legacy["processors"][0]["config"]["guidelines_prompt"]
    manager.repository.put("legacy", legacy, expected_revision=0)

    monkeypatch.setattr(evolve_config, "guidelines_prompt", "rules")
    plan = manager.resolve("legacy", revision=1)

    assert plan.manifest()["processors"][0]["config"]["guidelines_prompt"] == "default"


def test_a_profile_naming_an_unknown_prompt_is_not_published():
    profile = {"processors": [{"id": "g", "plugin": "evolve.guidelines", "config": {"guidelines_prompt": "typo"}}]}

    with pytest.raises(ProcessingError):
        _manager().put("bad-prompt", profile, expected_revision=0)
