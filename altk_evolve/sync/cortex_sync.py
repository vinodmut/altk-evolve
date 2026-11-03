"""
Cortex Sync - Turn sessions recorded by a Cortex proxy into Evolve trajectories.

Cortex (https://github.com/rossoctl/cortex) sits in front of local coding agents and records
every inference exchange as a parsed session event, served by its session API. This module:

1. Lists a session's turns from the cheap ``view=summary`` projection
2. Fetches the full event of each new main-agent turn and rebuilds OpenAI chat messages
3. Processes the new turns as one trajectory batch, with earlier turns as context
4. Advances a per-session cursor so later calls only learn from turns not yet seen

Both ingestion paths share ``ingest_session``: the pull path (``evolve sync cortex``) and the
push path (the Cortex ``evolve`` plugin notifying ``POST /api/cortex/sessions/{id}/ingest``).
The cursor and the batch identity make the two safe to combine.

Guidelines learned here are written with ``status: candidate``: session content includes tool
output the agent read, so nothing learned from it is served until a reviewer promotes it.
"""

import json
import hashlib
import logging
import os
import re
import tempfile
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import httpx

from altk_evolve.frontend.client.evolve_client import EvolveClient
from altk_evolve.processing import Trajectory, TrajectoryBatch, TrajectoryOutcome

logger = logging.getLogger("evolve.sync.cortex")

DEFAULT_CORTEX_URL = "http://127.0.0.1:47601"
SOURCE = "cortex"
CREATION_MODE = "auto-cortex"
# The session API caps a page at 2000 events.
PAGE_LIMIT = 2000
CONTEXT_TURNS = 10

# Harness-injected context, and guidelines Cortex itself injected: neither is the user's words,
# and learning from injected guidelines would feed Evolve its own output.
_STRIP_BLOCKS = re.compile(r"<evolve-guidelines\b[^>]*>.*?</evolve-guidelines>", re.DOTALL)
_REMINDERS = re.compile(r"<system-reminder\b[^>]*>.*?</system-reminder>", re.DOTALL)


def cortex_url() -> str:
    return os.environ.get("EVOLVE_CORTEX_URL", DEFAULT_CORTEX_URL)


def clean_text(text: str | None) -> str:
    """Remove Evolve's own injected guidelines, retaining task/environment constraints."""
    return _STRIP_BLOCKS.sub("", text or "").strip()


class CortexSessionClient:
    """Read-only client for a Cortex session API (``/v1/sessions``)."""

    def __init__(self, base_url: str | None = None, *, timeout: float = 30.0, transport: httpx.BaseTransport | None = None):
        # trust_env=False: Cortex is usually also the process's HTTPS_PROXY, and its own
        # session API must be reached directly.
        self._http = httpx.Client(base_url=base_url or cortex_url(), timeout=timeout, trust_env=False, transport=transport)

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params: Any) -> Any:
        response = self._http.get(path, params={k: v for k, v in params.items() if v is not None})
        response.raise_for_status()
        return response.json()

    def list_sessions(self) -> list[dict]:
        return self._get("/v1/sessions", archived="true").get("sessions") or []

    def summary_events(self, session_id: str) -> list[dict]:
        """Every event of a session without message bodies, oldest first."""
        events: dict[int, dict] = {}
        before = None
        while True:
            page = self._get(f"/v1/sessions/{session_id}", view="summary", limit=PAGE_LIMIT, before=before)
            batch = page.get("events") or []
            for event in batch:
                events[event["seq"]] = event
            oldest = min((e["seq"] for e in batch), default=None)
            held_from = page.get("oldestSeq") or 1
            if oldest is None or oldest <= held_from or len(batch) < PAGE_LIMIT:
                break
            before = oldest
        return [events[seq] for seq in sorted(events)]

    def event(self, session_id: str, seq: int) -> dict:
        return cast(dict, self._get(f"/v1/sessions/{session_id}/events/{seq}"))


def is_main_turn(event: dict) -> bool:
    """A settled response of the agent's main conversation.

    Excludes subagents, tunnels and the agent's tool-less side calls (titles, summaries),
    which Claude Code also tags ``agentRole: main``.
    """
    inference = event.get("inference")
    if not inference or event.get("phase") != "response" or event.get("subagent") or event.get("tunnel"):
        return False
    if not (event.get("client") or {}).get("name"):
        return False
    if inference.get("agentRole") not in (None, "", "main"):
        return False
    return bool(inference.get("toolCount") or inference.get("tools"))


def _message_count(event: dict) -> int:
    inference = event.get("inference") or {}
    return int(inference.get("messageCount") or len(inference.get("messages") or []))


def first_user_text(event: dict) -> str | None:
    for message in (event.get("inference") or {}).get("messages") or []:
        if message.get("role") == "user":
            text = _REMINDERS.sub("", clean_text(message.get("content"))).strip()
            if text:
                return text
    return None


def turns_to_messages(
    turns: list[dict],
    *,
    prev_message_count: int = 0,
    prev_tool_call_ids: tuple[str, ...] = (),
    previous_turn: dict | None = None,
    supporting_events: list[dict] | None = None,
) -> list[dict]:
    """Rebuild OpenAI chat messages from consecutive full main-turn response events.

    Cortex keeps only the text of each message in a request, so the history's tool calls are
    taken from each turn's own ``toolCalls`` and its tool results from ``toolResults``.
    """
    messages: list[dict] = []
    previous_inference = (previous_turn or {}).get("inference") or {}
    previous_history = previous_inference.get("messages")
    if previous_turn is not None:
        prev_message_count = len(previous_history or [])
        prev_tool_call_ids = tuple(c["id"] for c in previous_inference.get("toolCalls") or [] if c.get("id"))
    pending_calls = dict.fromkeys(prev_tool_call_ids)
    seen_results = {r.get("toolUseId") for r in previous_inference.get("toolResults") or []}
    supporting = iter(sorted(supporting_events or [], key=lambda e: e["seq"]))
    child = next(supporting, None)
    for turn in turns:
        while child is not None and child["seq"] < turn["seq"]:
            child_inference = child.get("inference") or {}
            observation = {
                "seq": child["seq"],
                "subagent": child.get("subagent"),
                "model": child_inference.get("model"),
                "messages": [{**m, "content": clean_text(m.get("content"))} for m in child_inference.get("messages") or []],
                "completion": clean_text(child_inference.get("completion")),
                "toolCalls": child_inference.get("toolCalls") or [],
                "toolResults": child_inference.get("toolResults") or [],
                "tools": child_inference.get("tools") or [],
            }
            messages.append(
                {
                    "role": "system",
                    "cortex_observation": True,
                    "content": "Cortex subagent observation (separate agent; supporting evidence, "
                    "not the main agent's user instructions):\n" + json.dumps(observation, ensure_ascii=False),
                }
            )
            child = next(supporting, None)
        inference = turn.get("inference") or {}
        history = inference.get("messages") or []
        if previous_history is not None:
            # Compare contents, not just lengths: compaction and same-length edits can
            # replace a user correction without changing the message count.
            prev_message_count = 0
            for old, current in zip(previous_history, history):
                # Injected guideline IDs and wire byte counts can change while
                # the conversation stays the same. Neither makes old input new.
                old_key = (old.get("role"), clean_text(old.get("content")), old.get("toolUseId"))
                current_key = (current.get("role"), clean_text(current.get("content")), current.get("toolUseId"))
                if old_key != current_key:
                    break
                prev_message_count += 1
        elif len(history) < prev_message_count:
            prev_message_count = 0
        results = {r.get("toolUseId"): r for r in inference.get("toolResults") or [] if r.get("toolUseId")}
        # OpenAI clients also put results in role=tool messages. Do not duplicate
        # the copy now provided in toolResults by current Cortex versions.
        for message in history:
            if message.get("role") == "tool" and message.get("toolUseId"):
                results.setdefault(message["toolUseId"], message)
        ordered_ids = [cid for cid in pending_calls if cid in results]
        ordered_ids.extend(cid for cid in results if cid not in pending_calls and cid not in seen_results)
        for call_id in ordered_ids:
            result = results[call_id]
            content = result.get("content") or ""
            if result.get("isError"):
                content = f"[error] {content}"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
            pending_calls.pop(call_id, None)
            seen_results.add(call_id)
        for message in history[prev_message_count:]:
            if message.get("role") not in ("user", "system", "developer"):
                continue
            text = clean_text(message.get("content"))
            if text:
                messages.append({"role": message["role"], "content": text})
        input_only = turn.get("phase") == "request"
        calls = [] if input_only else inference.get("toolCalls") or []
        assistant: dict[str, Any] = {"role": "assistant", "content": "" if input_only else clean_text(inference.get("completion"))}
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": call.get("id") or f"call_{turn['seq']}_{i}",
                    "type": "function",
                    "function": {"name": call.get("name") or "unknown", "arguments": call.get("arguments", "{}")},
                }
                for i, call in enumerate(calls)
            ]
        if assistant["content"] or calls:
            messages.append(assistant)
        pending_calls.update(dict.fromkeys(c["id"] for c in assistant.get("tool_calls", [])))
        prev_message_count = len(history)
        previous_history = history
        if input_only:
            messages.append(
                {
                    "role": "system",
                    "content": f"Cortex captured input at seq {turn['seq']}; no response was captured yet. "
                    "The observed tool outputs and user inputs do not establish task success.",
                    "cortex_observation": True,
                }
            )
        finish = inference.get("finishReason")
        status = turn.get("statusCode")
        if finish in ("length", "max_tokens", "incomplete", "content_filter") or (status and status >= 400) or turn.get("error"):
            messages.append(
                {
                    "role": "system",
                    "content": f"Cortex observed an incomplete or failed inference at seq {turn['seq']}: "
                    + json.dumps({"finishReason": finish, "statusCode": status, "error": turn.get("error")}),
                    "cortex_observation": True,
                }
            )
    return messages


class CortexCursorStore:
    """Per-session high-water marks: the last Cortex ``seq`` already learned from."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._lock = threading.Lock()

    def _load(self) -> dict[str, dict]:
        try:
            return cast(dict[str, dict], json.loads(self.path.read_text()))
        except FileNotFoundError:
            return {}

    def get(self, session_id: str) -> int:
        with self._lock:
            return int(self._load().get(session_id, {}).get("last_seq", 0))

    def set(self, session_id: str, last_seq: int) -> None:
        with self._lock:
            data = self._load()
            data[session_id] = {"last_seq": last_seq, "updated_at": datetime.now(UTC).isoformat()}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".cortex_cursors.")
            with os.fdopen(fd, "w") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp, self.path)


def default_cursor_store() -> CortexCursorStore:
    from altk_evolve.config.filesystem import filesystem_settings

    return CortexCursorStore(Path(filesystem_settings.data_dir) / "cortex_cursors.json")


@dataclass
class IngestResult:
    session_id: str
    status: str  # "processed" | "dry-run" | "up-to-date" | "too-few-turns"
    new_turns: int = 0
    first_seq: int | None = None
    last_seq: int | None = None
    guidelines: list[str] = field(default_factory=list)
    trajectory: dict | None = None
    trajectories: list[dict] | None = None


_session_locks: dict[str, threading.Lock] = {}
_session_locks_guard = threading.Lock()


def _session_lock(session_id: str) -> threading.Lock:
    with _session_locks_guard:
        return _session_locks.setdefault(session_id, threading.Lock())


def ingest_session(
    client: EvolveClient | None,
    cortex: CortexSessionClient,
    session_id: str,
    *,
    namespace_id: str,
    cursors: CortexCursorStore,
    min_new_turns: int = 2,
    max_turns: int = 40,
    dry_run: bool = False,
    outcome: TrajectoryOutcome | None = None,
) -> IngestResult:
    """Drain new turns in bounded batches, checkpointing only completed batches.

    An explicit evaluator outcome replays the available session with an outcome-owned
    revision, including when its turns were already ingested. Identical feedback is
    deduplicated by the processing pipeline. No success is inferred from agent text.
    """
    if min_new_turns < 1 or max_turns < 1:
        raise ValueError("min_new_turns and max_turns must be positive")
    with _session_lock(session_id):
        cursor = cursors.get(session_id)
        summaries = cortex.summary_events(session_id)
        turns = [e for e in summaries if is_main_turn(e)]
        last_response = max((t["seq"] for t in turns), default=0)
        # A failed/aborted final inference can still carry the last tool output in
        # its request. Learn that observed input without inventing a response.
        turns.extend(
            e for e in summaries if e["seq"] > last_response and e.get("phase") == "request" and is_main_turn({**e, "phase": "response"})
        )
        turns.sort(key=lambda e: e["seq"])
        new = [t for t in turns if t["seq"] > cursor]
        selected = turns if outcome is not None else new
        if not selected:
            return IngestResult(session_id, "up-to-date")
        if outcome is None and len(new) < min_new_turns and cursor == 0:
            return IngestResult(session_id, "too-few-turns", new_turns=len(new))

        cache: dict[int, dict] = {}

        def full_event(summary: dict) -> dict:
            seq = summary["seq"]
            if seq not in cache:
                cache[seq] = cortex.event(session_id, seq)
            return cache[seq]

        # The first available task is stable across later context compactions.
        first_event = full_event(turns[0])
        original_task = first_user_text(first_event)
        original_constraints = []
        for message in (first_event.get("inference") or {}).get("messages") or []:
            text = clean_text(message.get("content"))
            if text and message.get("role") in ("system", "developer"):
                original_constraints.append({"role": message["role"], "content": text})
            elif message.get("role") == "user":
                original_constraints.extend({"role": "system", "content": m.group(0)} for m in _REMINDERS.finditer(text))
        revision = "2"
        if outcome is not None:
            revision += ":outcome:" + hashlib.sha256(outcome.model_dump_json().encode()).hexdigest()
        trajectories: list[dict] = []
        guidelines: list[str] = []
        for start in range(0, len(selected), max_turns):
            window = selected[start : start + max_turns]
            previous = [t for t in turns if t["seq"] < window[0]["seq"]]
            full = [full_event(t) for t in window]
            earlier = [full_event(t) for t in previous[-CONTEXT_TURNS:]]
            context: list[dict[str, Any]] = [{"role": "user", "content": original_task}] if original_task else []
            context.extend(original_constraints)
            context.extend(turns_to_messages(earlier))
            lower_seq = previous[-1]["seq"] if previous else 0
            child_summaries = [
                e
                for e in summaries
                if lower_seq < e["seq"] < window[-1]["seq"]
                and e.get("phase") == "response"
                and e.get("inference")
                and not e.get("tunnel")
                and (e.get("subagent") or e["inference"].get("agentRole") == "subagent")
            ]
            messages = turns_to_messages(
                full, previous_turn=earlier[-1] if earlier else None, supporting_events=[full_event(e) for e in child_summaries]
            )
            tools = [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": tool.get("parameters") or {"type": "object"},
                    },
                }
                for tool in (full[-1].get("inference") or {}).get("tools") or []
                if tool.get("name")
            ]
            # Preserve changes to schemas/descriptions, not just the final manifest.
            manifest = None
            for event in full:
                current = (event.get("inference") or {}).get("tools") or []
                if current != manifest:
                    context.append(
                        {
                            "role": "system",
                            "content": f"Tool definitions at Cortex seq {event['seq']}: " + json.dumps(current, ensure_ascii=False),
                            "cortex_observation": True,
                        }
                    )
                    manifest = current
            agent = (window[-1].get("client") or {}).get("name")
            first_seq, last_seq = window[0]["seq"], window[-1]["seq"]
            capture = {
                "adapter_revision": 2,
                "excluded_events": len(summaries) - len(turns),
                "subagent_seqs": [e["seq"] for e in child_summaries],
                "turns": [
                    {
                        "seq": event["seq"],
                        "phase": event.get("phase"),
                        **{k: event[k] for k in ("statusCode", "error", "durationMs") if k in event},
                        "inference": {
                            k: value
                            for k, value in (event.get("inference") or {}).items()
                            if k not in ("messages", "tools", "toolCalls", "toolResults", "completion")
                        },
                    }
                    for event in full
                ],
                "limits": [
                    "Cortex's parsed API retains text, not image/audio payloads or hidden reasoning.",
                    "Subagents are separate supporting observations; tool-less side calls are excluded.",
                    "Tool output is visible only after the agent sends it through the proxy; no final request means no final tool result.",
                ],
            }
            trajectory = Trajectory(
                messages=messages,
                context_messages=context,
                tools=tools,
                batch=TrajectoryBatch(
                    source=SOURCE, conversation_id=session_id, batch_id=f"{first_seq}-{last_seq}", revision=revision, scope=SOURCE
                ),
                trace_id=f"{SOURCE}:{session_id}:{last_seq}",
                model=(full[-1].get("inference") or {}).get("model"),
                metadata={
                    "status": "candidate",
                    "creation_mode": CREATION_MODE,
                    "agent_id": agent,
                    "session_id": session_id,
                    "cortex_capture": capture,
                },
                outcome=outcome,
            )
            if dry_run:
                trajectories.append(trajectory.model_dump(mode="json"))
                cache = {t["seq"]: cache[t["seq"]] for t in window[-CONTEXT_TURNS:]}
                continue
            assert client is not None
            client.ensure_namespace(namespace_id)
            result = client.process_trajectory(trajectory, namespace_id=namespace_id)
            cursors.set(session_id, max(cursor, last_seq))
            guidelines.extend(u["id"] for u in result.updates if u.get("type") == "guideline" and u.get("event") in ("ADD", "UPDATE"))
            cache = {t["seq"]: cache[t["seq"]] for t in window[-CONTEXT_TURNS:]}

        first_seq, last_seq = selected[0]["seq"], selected[-1]["seq"]
        if dry_run:
            return IngestResult(session_id, "dry-run", len(new), first_seq, last_seq, trajectory=trajectories[0], trajectories=trajectories)
        logger.info("cortex session %s: learned from seq %s-%s, %d guideline change(s)", session_id, first_seq, last_seq, len(guidelines))
        return IngestResult(session_id, "processed", len(new), first_seq, last_seq, guidelines)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def sync_cortex(
    client: EvolveClient | None,
    cortex: CortexSessionClient,
    *,
    namespace_id: str,
    cursors: CortexCursorStore,
    idle_minutes: float = 15,
    session_ids: list[str] | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    **kwargs: Any,
) -> list[IngestResult]:
    """Ingest every session idle for at least ``idle_minutes`` (or exactly ``session_ids``)."""
    if session_ids is None:
        cutoff = (now or datetime.now(UTC)) - timedelta(minutes=idle_minutes)
        session_ids = []
        for summary in cortex.list_sessions():
            updated = _parse_time(summary.get("updatedAt"))
            sid = summary.get("id") or ""
            if not summary.get("agent") or sid == "default" or sid.startswith("pending:"):
                continue
            if updated is not None and updated <= cutoff:
                session_ids.append(sid)
    results = []
    for sid in session_ids:
        try:
            results.append(ingest_session(client, cortex, sid, namespace_id=namespace_id, cursors=cursors, dry_run=dry_run, **kwargs))
        except Exception as exc:  # one bad session must not stop the sync
            logger.warning("cortex session %s: ingest failed: %s", sid, exc)
            results.append(IngestResult(sid, f"error: {exc}"))
    return results
