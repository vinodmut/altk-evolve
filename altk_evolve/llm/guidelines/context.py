"""Bound history for extraction prompts without altering inference replay inputs."""

import json


def tool_results_by_call_id(messages: list[dict]) -> dict[str, str]:
    """Match observations to actions independently of parallel result order."""
    results = {}
    for message in messages:
        if message.get("role") == "tool" and message.get("tool_call_id"):
            value = message.get("content", "")
            results[message["tool_call_id"]] = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return results


def render_action_with_result(action: str, result: str, *, max_chars: int) -> str:
    """Bound action and observation together, reserving space for observed output."""
    if not result:
        return action[:max_chars] + ("..." if len(action) > max_chars else "")
    action_budget = min(len(action), max_chars // 2)
    result_budget = max_chars - action_budget
    action_text = action[:action_budget] + ("..." if len(action) > action_budget else "")
    observation = result[:result_budget] + ("..." if len(result) > result_budget else "")
    return action_text + "\nTool result (observed output):\n" + observation


def render_supporting_context(messages: list[dict] | None, *, max_chars: int = 20000) -> str:
    """Render recent history within a character budget; replay uses original messages."""
    if not messages:
        return ""
    parts = []
    remaining = max_chars - len("[earlier history omitted]\n")
    for message in reversed(messages[-50:]):
        rendered = json.dumps(message, ensure_ascii=False)
        if len(rendered) > 2000:
            rendered = rendered[:2000] + " [truncated]"
        if len(rendered) + 1 > remaining:
            break
        parts.append(rendered)
        remaining -= len(rendered) + 1
    prefix = "[earlier history omitted]\n" if len(parts) < len(messages) else ""
    return prefix + "\n".join(reversed(parts))
