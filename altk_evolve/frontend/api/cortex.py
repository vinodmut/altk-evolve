"""Authenticated REST surface for the Cortex ``evolve`` plugin.

The rest of Evolve's local HTTP API has no authentication. A guideline store is a steering
surface for every agent behind the proxy, so this router refuses to serve at all until a
bearer token is configured (``EVOLVE_API_TOKEN`` or ``EVOLVE_API_TOKEN_FILE``), and is served
on its own by ``evolve cortex serve`` without the unauthenticated dashboard routes.

Only ``active`` guidelines are served: candidates learned from sessions wait for review.
"""

import hmac
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from altk_evolve.processing import TrajectoryOutcome

logger = logging.getLogger("evolve.api.cortex")


def configured_token() -> str | None:
    token = os.environ.get("EVOLVE_API_TOKEN")
    if not token and (path := os.environ.get("EVOLVE_API_TOKEN_FILE")):
        try:
            token = Path(path).expanduser().read_text()
        except OSError as exc:
            logger.error("cannot read EVOLVE_API_TOKEN_FILE: %s", exc)
    token = (token or "").strip()
    return token or None


def require_token(authorization: str | None = Header(default=None)) -> None:
    expected = configured_token()
    if expected is None:
        raise HTTPException(503, "EVOLVE_API_TOKEN is not configured; the Cortex API is disabled")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(supplied.strip().encode(), expected.encode()):
        raise HTTPException(401, "invalid bearer token", headers={"WWW-Authenticate": "Bearer"})


def _client():
    from altk_evolve.frontend.mcp.mcp_server import get_client

    return get_client()


def _namespace() -> str:
    from altk_evolve.config.evolve import evolve_config

    return evolve_config.namespace_id


def _ingest(session_id: str, *, outcome: TrajectoryOutcome | None = None) -> None:
    from altk_evolve.sync.cortex_sync import CortexSessionClient, default_cursor_store, ingest_session

    cortex = CortexSessionClient()
    try:
        ingest_session(_client(), cortex, session_id, namespace_id=_namespace(), cursors=default_cursor_store(), outcome=outcome)
    except Exception:
        logger.exception("cortex session %s: ingest failed", session_id)
    finally:
        cortex.close()


# Replaceable in tests; runs the ingest off the request thread.
run_in_background: Callable[[Callable[[], None]], None] = lambda fn: threading.Thread(target=fn, daemon=True).start()  # noqa: E731


class GuidelinesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: str = Field(min_length=1, max_length=20000)
    session_id: str | None = None
    agent: str | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)


class IngestRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent: str | None = None
    outcome: TrajectoryOutcome | None = None


def _guideline_payload(entity) -> dict[str, Any]:
    metadata = entity.metadata or {}
    return {
        "id": str(entity.id),
        "content": entity.content if isinstance(entity.content, str) else str(entity.content),
        "rationale": metadata.get("rationale"),
        "memory_revision": metadata.get("memory_revision"),
        "source_task_id": metadata.get("source_task_id"),
        "creation_mode": metadata.get("creation_mode"),
        "support": metadata.get("support"),
    }


router = APIRouter(prefix="/api/cortex", dependencies=[Depends(require_token)])


@router.post("/guidelines")
def guidelines(request: GuidelinesRequest) -> dict[str, Any]:
    namespace_id = _namespace()
    client = _client()
    if not client.namespace_exists(namespace_id):
        return {"namespace_id": namespace_id, "guidelines": []}
    selection = client.select_guidelines(namespace_id, request.task, top_k=request.top_k, statuses={"active"})
    selected = selection.all
    logger.info("cortex guidelines: session=%s agent=%s -> %d", request.session_id, request.agent, len(selected))
    return {"namespace_id": namespace_id, "guidelines": [_guideline_payload(e) for e in selected]}


@router.post("/sessions/{session_id}/ingest", status_code=202)
def ingest(session_id: str, request: IngestRequest | None = None) -> dict[str, Any]:
    logger.info("cortex session %s: idle notification (agent=%s)", session_id, request.agent if request else None)
    if request and request.outcome is not None:
        run_in_background(lambda: _ingest(session_id, outcome=request.outcome))
    else:
        run_in_background(lambda: _ingest(session_id))
    return {"session_id": session_id, "accepted": True}


def create_app() -> FastAPI:
    app = FastAPI(title="Evolve Cortex API")
    app.include_router(router)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app
