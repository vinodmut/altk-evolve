"""Bound history for extraction prompts without altering inference replay inputs."""

import json


def bounded_text(text: str, max_chars: int) -> str:
    """Keep the start and end, with an explicit omission marker, within the budget."""
    if len(text) <= max_chars:
        return text
    if max_chars < 64:
        marker = "..."[:max_chars]
    else:
        marker = f"\n... [{len(text)} characters total; middle omitted] ...\n"
    available = max(0, max_chars - len(marker))
    head = (available + 1) // 2
    tail = available - head
    return text[:head] + marker + (text[-tail:] if tail else "")


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
    action_text = bounded_text(action, action_budget)
    observation = bounded_text(result, result_budget)
    return action_text + "\nTool result (observed output):\n" + observation


def render_input_evidence(
    messages: list[dict],
    *,
    context_messages: list[dict] | None = None,
    tools: list[dict] | None = None,
    execution_metadata: dict | None = None,
    max_chars: int = 20000,
) -> str:
    """Expose user updates, constraints and unpaired outputs without renumbering agent steps.

    An output for an earlier batch is new evidence even though its action is context.
    Unknown call IDs are labelled rather than assigned an invented action. Positions are
    message positions, independent of the action indices segmentation and replay use.
    """
    parts = []

    def message_calls(message):
        calls = list(message.get("tool_calls") or [])
        if isinstance(message.get("content"), list):
            calls.extend(item for item in message["content"] if item.get("type") == "function_call")
        return calls

    calls = {}
    for message in [*(context_messages or []), *messages]:
        for call in message_calls(message):
            calls[call.get("id")] = call.get("function", {})
    new_call_ids = {call.get("id") for message in messages for call in message_calls(message)}
    task_seen = any(m.get("role") == "user" and m.get("content") for m in context_messages or [])
    for position, message in enumerate(messages, 1):
        role, content = message.get("role"), message.get("content")
        if role == "user" and not task_seen:
            task_seen = True
            continue  # already rendered as the task instruction
        if role in ("user", "system", "developer") and content:
            parts.append(f"Message {position} - {role} input:\n" + bounded_text(str(content), 4000))
        elif role == "tool" and message.get("tool_call_id") not in new_call_ids:
            call_id = message.get("tool_call_id")
            call = calls.get(call_id)
            label = f"earlier action {json.dumps(call, ensure_ascii=False)}" if call else "action unavailable in captured context"
            parts.append(f"Message {position} - observed tool output ({call_id}; {label}):\n" + bounded_text(str(content or ""), 4000))
    if tools:
        parts.insert(
            0,
            "Available tool definitions (constraints, not executed actions):\n" + bounded_text(json.dumps(tools, ensure_ascii=False), 8000),
        )
    if execution_metadata:
        parts.insert(
            0,
            "Cortex capture and execution telemetry (reported usage/status, not a ground-truth task evaluation):\n"
            + bounded_text(json.dumps(execution_metadata, ensure_ascii=False), 4000),
        )
    return bounded_text("\n\n".join(parts), max_chars)


def render_supporting_context(messages: list[dict] | None, *, max_chars: int = 20000) -> str:
    """Render recent history within a character budget; replay uses original messages."""
    if not messages:
        return ""
    # Preserve the original task/rules as well as the recent call that a new result
    # answers. Truncation is a prompt concern: the trajectory retains full content.
    parts = [bounded_text(json.dumps(message, ensure_ascii=False), 4000) for message in messages]
    return bounded_text("\n".join(parts), max_chars)
