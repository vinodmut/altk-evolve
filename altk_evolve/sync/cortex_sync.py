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
from altk_evolve.processing import Trajectory, TrajectoryBatch

logger = logging.getLogger("evolve.sync.cortex")

DEFAULT_CORTEX_URL = "http://127.0.0.1:47601"
SOURCE = "cortex"
CREATION_MODE = "auto-cortex"
# The session API caps a page at 2000 events.
PAGE_LIMIT = 2000
MAX_TOOL_RESULT_CHARS = 4000
MAX_USER_CHARS = 8000

# Harness-injected context, and guidelines Cortex itself injected: neither is the user's words,
# and learning from injected guidelines would feed Evolve its own output.
_STRIP_BLOCKS = re.compile(r"<(system-reminder|evolve-guidelines)\b[^>]*>.*?</\1>", re.DOTALL)


def cortex_url() -> str:
    return os.environ.get("EVOLVE_CORTEX_URL", DEFAULT_CORTEX_URL)


def clean_text(text: str | None) -> str:
    """Remove injected blocks and surrounding whitespace from a message's text."""
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
            text = clean_text(message.get("content"))
            if text:
                return text[:MAX_USER_CHARS]
    return None


def turns_to_messages(turns: list[dict], *, prev_message_count: int = 0, prev_tool_call_ids: tuple[str, ...] = ()) -> list[dict]:
    """Rebuild OpenAI chat messages from consecutive full main-turn response events.

    Cortex keeps only the text of each message in a request, so the history's tool calls are
    taken from each turn's own ``toolCalls`` and its tool results from ``toolResults``.
    """
    messages: list[dict] = []
    pending_calls = set(prev_tool_call_ids)
    for turn in turns:
        inference = turn.get("inference") or {}
        history = inference.get("messages") or []
        if len(history) < prev_message_count:  # the agent compacted its context
            prev_message_count = 0
        results = {r.get("toolUseId"): r for r in inference.get("toolResults") or [] if r.get("toolUseId")}
        for call_id in sorted(pending_calls & results.keys()):
            result = results[call_id]
            content = (result.get("content") or "")[:MAX_TOOL_RESULT_CHARS]
            if result.get("isError"):
                content = f"[error] {content}"
            messages.append({"role": "tool", "tool_call_id": call_id, "content": content})
        for message in history[prev_message_count:]:
            if message.get("role") != "user":
                continue
            text = clean_text(message.get("content"))
            if text:
                messages.append({"role": "user", "content": text[:MAX_USER_CHARS]})
        calls = inference.get("toolCalls") or []
        assistant: dict[str, Any] = {"role": "assistant", "content": inference.get("completion") or ""}
        if calls:
            assistant["tool_calls"] = [
                {
                    "id": call.get("id") or f"call_{turn['seq']}_{i}",
                    "type": "function",
                    "function": {"name": call.get("name") or "unknown", "arguments": call.get("arguments") or "{}"},
                }
                for i, call in enumerate(calls)
            ]
        if assistant["content"] or calls:
            messages.append(assistant)
        pending_calls = {c["id"] for c in assistant.get("tool_calls", [])}
        prev_message_count = len(history)
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
) -> IngestResult:
    """Learn candidate guidelines from the turns of ``session_id`` after its cursor."""
    with _session_lock(session_id):
        cursor = cursors.get(session_id)
        turns = [e for e in cortex.summary_events(session_id) if is_main_turn(e)]
        new = [t for t in turns if t["seq"] > cursor]
        if not new:
            return IngestResult(session_id, "up-to-date")
        if len(new) < min_new_turns:
            return IngestResult(session_id, "too-few-turns", new_turns=len(new))

        window = new[-max_turns:]
        previous = [t for t in turns if t["seq"] < window[0]["seq"]]
        full = [cortex.event(session_id, t["seq"]) for t in window]
        prev_calls: tuple[str, ...] = ()
        if previous:
            prev_full = cortex.event(session_id, previous[-1]["seq"])
            prev_calls = tuple(c.get("id") for c in (prev_full.get("inference") or {}).get("toolCalls") or [] if c.get("id"))
        messages = turns_to_messages(
            full, prev_message_count=_message_count(previous[-1]) if previous else 0, prev_tool_call_ids=prev_calls
        )
        task = first_user_text(full[0])
        context = [{"role": "user", "content": task}] if task and previous else []

        agent = (window[-1].get("client") or {}).get("name")
        first_seq, last_seq = window[0]["seq"], window[-1]["seq"]
        trajectory = Trajectory(
            messages=messages,
            context_messages=context,
            batch=TrajectoryBatch(source=SOURCE, conversation_id=session_id, batch_id=f"{first_seq}-{last_seq}", scope=SOURCE),
            trace_id=f"{SOURCE}:{session_id}:{last_seq}",
            model=(window[-1].get("inference") or {}).get("model"),
            metadata={"status": "candidate", "creation_mode": CREATION_MODE, "agent_id": agent, "session_id": session_id},
        )
        if dry_run:
            return IngestResult(session_id, "dry-run", len(new), first_seq, last_seq, trajectory=trajectory.model_dump(mode="json"))

        assert client is not None
        client.ensure_namespace(namespace_id)
        result = client.process_trajectory(trajectory, namespace_id=namespace_id)
        cursors.set(session_id, last_seq)
        guidelines = [u["id"] for u in result.updates if u.get("type") == "guideline" and u.get("event") in ("ADD", "UPDATE")]
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
