"""Container-local Cortex, full Evolve, and Anthropic gateway bridge."""

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
RESULTS = Path(os.environ.get("DEMO_RESULTS", "/results"))
RUNTIME = RESULTS / "runtime"
STATE = RESULTS / "state.json"
API = "http://127.0.0.1:47801"
MCP = "http://127.0.0.1:18201/sse"
BRIDGE = "http://127.0.0.1:18202"
CA = RUNTIME / "ca"
UV = ["uv", "run", "--offline", "--no-sync", "--project", str(ROOT)]


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    temporary.replace(path)


def load_state():
    return read_json(STATE)


def save_state(state):
    write_json(STATE, state)


def environment():
    """Read the mounted credential file; never inherit the host's credentials/proxy."""
    values = dotenv_values(os.environ.get("DEMO_CREDENTIALS", "/run/secrets/demo.env"))
    required = ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "OPENAI_BASE_URL", "OPENAI_API_KEY", "EVOLVE_MODEL_NAME")
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise RuntimeError("Fill in .env: " + ", ".join(missing))
    for name in ("ANTHROPIC_BASE_URL", "OPENAI_BASE_URL"):
        url = httpx.URL(values[name])
        if url.scheme not in {"http", "https"} or not url.host or url.userinfo:
            raise RuntimeError(f"{name} must be an HTTP(S) URL without embedded credentials.")
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.lower().endswith("_proxy") and not k.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_", "EVOLVE_"))
    }
    env.update({name: values[name] for name in (*required, "EVOLVE_CUSTOM_LLM_PROVIDER") if values.get(name)})
    env["ANTHROPIC_BASE_URL"] = env["ANTHROPIC_BASE_URL"].rstrip("/").removesuffix("/v1")
    env.update(
        EVOLVE_CUSTOM_LLM_PROVIDER=values.get("EVOLVE_CUSTOM_LLM_PROVIDER") or "openai",
        EVOLVE_GUIDELINES_MODEL=env["EVOLVE_MODEL_NAME"],
        EVOLVE_BACKEND="filesystem",
        EVOLVE_DATA_DIR=str(RUNTIME / "data"),
        EVOLVE_SQLITE_PATH=str(RUNTIME / "entities.sqlite.db"),
        EVOLVE_NAMESPACE_ID="photo-reviewed",
        EVOLVE_GUIDELINES_MODE="consistency",
        EVOLVE_INJECTION_MODE="retrieval",
        EVOLVE_EMBEDDING_PROVIDER="fastembed",
        EVOLVE_OTEL_ENABLED="false",
        EVOLVE_RETENTION_SCHEDULER_ENABLED="false",
        EVOLVE_HOOKS_CONFIG="",
        FASTEMBED_CACHE_PATH=os.environ.get("FASTEMBED_CACHE_PATH", "/opt/embeddings"),
    )
    return env


def configure_process():
    env = environment()
    for name in list(os.environ):
        if name.lower().endswith("_proxy") or name.startswith(("ANTHROPIC_", "OPENAI_", "CLAUDE_", "EVOLVE_")):
            del os.environ[name]
    os.environ.update(env)
    return env


def cortex_config(inject=False):
    return {
        "mode": "proxy-sidecar",
        "listener": {
            "roles": ["forward"],
            "bind_loopback_only": True,
            "forward_proxy_addr": "127.0.0.1:47800",
            "session_api_addr": "127.0.0.1:47801",
            "transparent_proxy_addr": "127.0.0.1:47803",
            "health_addr": "127.0.0.1:47804",
        },
        "stats": {"address": "127.0.0.1:47802"},
        "tls_bridge": {"mode": "enabled", "ca_dir": str(CA), "generate_ca": False},
        "cost_ledger": {"enabled": False},
        "pipeline": {
            "outbound": {
                "plugins": [
                    {"name": "inference-parser"},
                    {"name": "mcp-parser"},
                    {"name": "a2a-parser"},
                    {
                        "name": "evolve",
                        "config": {
                            "endpoint": MCP,
                            "agents": ["claude-code"],
                            "serve_namespace": "photo-reviewed",
                            "learn_namespace": "photo-candidates",
                            "processing_profile": "photo-demo",
                            "learn": False,
                            "inject": inject,
                            "idle_after_seconds": 60,
                            "cache_ttl_seconds": 30,
                            "timeout_ms": 30000,
                            "learn_timeout_seconds": 180,
                        },
                    },
                ]
            }
        },
    }


def set_injection(enabled):
    path = RUNTIME / "cortex.yaml"
    path.write_text(yaml.safe_dump(cortex_config(enabled), sort_keys=False))
    # Cortex watches the file. Read back the effective pipeline before launching.
    with httpx.Client(trust_env=False, timeout=2) as client:
        for _ in range(30):
            pipeline = client.get(API + "/v1/pipeline").json()
            plugin = next((p for p in pipeline.get("outbound", []) if p["name"] == "evolve"), {})
            config = plugin.get("config", {})
            if config.get("inject") == enabled and config.get("learn") is False:
                return
            time.sleep(0.5)
    raise RuntimeError("Cortex did not confirm the injection setting. Inspect runtime/cortex.log.")


def health():
    if not (RUNTIME / "ready").is_file():
        raise RuntimeError("Services are starting. Inspect ./demo logs.")
    with httpx.Client(trust_env=False, timeout=3) as client:
        client.get(API + "/v1/pipeline").raise_for_status()
        client.get(BRIDGE + "/health").raise_for_status()
        with client.stream("GET", MCP) as stream:
            stream.raise_for_status()


def serve():
    env = configure_process()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    ready = RUNTIME / "ready"
    ready.unlink(missing_ok=True)
    CA.mkdir(exist_ok=True)
    if not (CA / "tls.key").exists():
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-sha256",
                "-days",
                "30",
                "-subj",
                "/CN=Cortex Photo Demo",
                "-addext",
                "basicConstraints=critical,CA:TRUE",
                "-keyout",
                str(CA / "tls.key"),
                "-out",
                str(CA / "tls.crt"),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (CA / "tls.key").chmod(0o600)
        (CA / "ca.crt").write_bytes((CA / "tls.crt").read_bytes())
    if not STATE.exists():
        save_state(
            {
                "created_at": datetime.now(UTC).isoformat(),
                "model": env["ANTHROPIC_MODEL"],
                "learning_model": env["EVOLVE_MODEL_NAME"],
                "sessions": {},
                "promoted": [],
            }
        )
    state = load_state()
    if state["model"] != env["ANTHROPIC_MODEL"] or state["learning_model"] != env["EVOLVE_MODEL_NAME"]:
        raise RuntimeError("The models differ from this run. Use a fresh results directory for a new comparison.")
    (RUNTIME / "cortex.yaml").write_text(yaml.safe_dump(cortex_config(False), sort_keys=False))
    commands = {
        "evolve": [*UV, "evolve-mcp", "--transport", "sse", "--port", "18201"],
        "cortex": ["cortex", "--config", str(RUNTIME / "cortex.yaml")],
        "bridge": [*UV, "python", str(Path(__file__)), "bridge"],
    }
    children = []

    def stop(*_):
        ready.unlink(missing_ok=True)
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()

    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    signal.signal(signal.SIGINT, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        for name, command in commands.items():
            with (RUNTIME / f"{name}.log").open("a") as log:
                children.append(subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log))
        with httpx.Client(trust_env=False, timeout=2) as client:
            for _ in range(120):
                if any(child.poll() is not None for child in children):
                    raise RuntimeError("A service exited. Read results/runtime/{cortex,evolve,bridge}.log.")
                try:
                    client.get(API + "/v1/pipeline").raise_for_status()
                    client.get(BRIDGE + "/health").raise_for_status()
                    with client.stream("GET", MCP) as stream:
                        stream.raise_for_status()
                    ready.touch()
                    print("Ready: Claude Code → Cortex → gateway bridge; full Evolve MCP is listening.", flush=True)
                    break
                except httpx.HTTPError:
                    time.sleep(1)
            else:
                raise RuntimeError("Services did not start within 120 seconds.")
        while all(child.poll() is None for child in children):
            time.sleep(1)
        raise RuntimeError("A service stopped. Inspect results/runtime/*.log.")
    except KeyboardInterrupt:
        pass
    finally:
        stop()


def flatten_system(body):
    system = body.get("system")
    if isinstance(system, list):
        if any(not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str) for block in system):
            raise ValueError("This demo bridge supports text system blocks only.")
        return {**body, "system": "\n\n".join(block["text"] for block in system)}
    return body


def serve_bridge():
    import uvicorn
    from fastapi import FastAPI, Request
    from starlette.responses import JSONResponse, StreamingResponse

    gateway = environment()["ANTHROPIC_BASE_URL"]
    app = FastAPI()
    hop = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}

    @app.get("/health")
    async def bridge_health():
        return {"ready": True}

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def forward(path: str, request: Request):
        content = await request.body()
        if request.method == "POST" and path in {"v1/messages", "v1/messages/count_tokens"}:
            try:
                content = json.dumps(flatten_system(json.loads(content))).encode()
            except (ValueError, TypeError):
                return JSONResponse(
                    {"error": {"type": "invalid_request_error", "message": "This demo supports text system blocks only."}}, status_code=400
                )
        headers = {k: v for k, v in request.headers.items() if k.lower() not in hop}
        client = httpx.AsyncClient(trust_env=False, timeout=180)
        upstream = client.build_request(request.method, gateway + "/" + path, headers=headers, content=content, params=request.query_params)
        try:
            response = await client.send(upstream, stream=True)
        except httpx.HTTPError:
            await client.aclose()
            return JSONResponse(
                {"error": {"type": "api_error", "message": "The demo bridge could not reach the configured gateway."}}, status_code=502
            )

        async def stream():
            try:
                async for chunk in response.aiter_bytes():
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        returned = {k: v for k, v in response.headers.items() if k.lower() not in hop}
        return StreamingResponse(stream(), status_code=response.status_code, headers=returned)

    uvicorn.run(app, host="127.0.0.1", port=18202, log_level="warning", access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["serve", "bridge", "health"])
    args = parser.parse_args()
    try:
        {"serve": serve, "bridge": serve_bridge, "health": health}[args.command]()
    except RuntimeError as exc:
        parser.exit(1, str(exc) + "\n")
