"""Which prompt the standard guideline generator renders.

A prompt is named by ``guidelines_prompt`` (``EVOLVE_GUIDELINES_PROMPT``, or a processing
profile's guideline processor config): either a built-in name below, or the path of a Jinja2
file that takes the same variables as ``generate_guidelines.jinja2``. Built-ins live with the
generator in ``altk_evolve/llm/guidelines/prompts``.

Resolving a name touches only the filesystem, so the config models can validate it without
importing the generator.
"""

from __future__ import annotations

from pathlib import Path

DEFAULT_GUIDELINES_PROMPT = "default"

PROMPTS_DIR = Path(__file__).parents[1] / "llm" / "guidelines" / "prompts"

# Name -> file in PROMPTS_DIR. "default" is the prompt every earlier release used.
BUILTIN_GUIDELINES_PROMPTS: dict[str, str] = {
    "default": "generate_guidelines.jinja2",
    # States each requirement the evidence spells out (a hook's or a check's error) as a rule
    # to follow before the action, keeping the project's own files, commands and formats.
    "rules": "generate_guidelines_rules.jinja2",
}


def resolve_guidelines_prompt(name: str) -> Path:
    """Return the template file for a built-in name or a path to a Jinja2 file.

    Raises ``ValueError`` for an unknown name or a path that is not a readable file, so a
    bad setting fails when a profile is published or the runtime is built, not at the first
    trajectory.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError("guidelines_prompt must be a built-in prompt name or a path to a Jinja2 file")
    name = name.strip()
    if name in BUILTIN_GUIDELINES_PROMPTS:
        return PROMPTS_DIR / BUILTIN_GUIDELINES_PROMPTS[name]
    path = Path(name).expanduser()
    if path.suffix in (".jinja2", ".j2", ".jinja") or "/" in name:
        if not path.is_file():
            raise ValueError(f"guidelines_prompt file not found: {path}")
        return path
    known = ", ".join(sorted(BUILTIN_GUIDELINES_PROMPTS))
    raise ValueError(f"Unknown guidelines_prompt {name!r}: use one of {known}, or a path to a Jinja2 file")
