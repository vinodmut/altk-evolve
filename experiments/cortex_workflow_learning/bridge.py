"""Isolated compatibility bridge for gateways that ignore later system blocks.

Cortex runs BEFORE this bridge. The bridge changes only Anthropic's system
text representation; tools, messages and streamed responses pass through.
Credentials and the real gateway URL are supplied through the environment.
"""

import json
import os

import httpx
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse

app = FastAPI()
HOP_HEADERS = {"host", "content-length", "connection", "transfer-encoding", "content-encoding"}


def flatten_system(body):
    """Preserve every text block in order, including Cortex's appended block."""
    system = body.get("system")
    if not isinstance(system, list):
        return body
    if any(not isinstance(block, dict) or block.get("type") != "text" or not isinstance(block.get("text"), str) for block in system):
        raise ValueError("The compatibility bridge supports text system blocks only")
    return {**body, "system": "\n\n".join(block["text"] for block in system)}


@app.api_route("/v1/messages", methods=["POST"])
async def messages(request: Request):
    body = flatten_system(await request.json())
    headers = {key: value for key, value in request.headers.items() if key.lower() not in HOP_HEADERS}
    client = httpx.AsyncClient(trust_env=False, timeout=180)
    upstream = client.build_request("POST", os.environ["WORKFLOW_REAL_GATEWAY"].rstrip("/") + "/v1/messages", headers=headers, json=body)
    try:
        response = await client.send(upstream, stream=True)
    except BaseException:
        await client.aclose()
        raise

    async def stream():
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            await client.aclose()

    returned_headers = {key: value for key, value in response.headers.items() if key.lower() not in HOP_HEADERS}
    return StreamingResponse(stream(), status_code=response.status_code, headers=returned_headers)


if __name__ == "__main__":
    import uvicorn

    # No request-body or header logging, and no credential-bearing configuration.
    print(json.dumps({"bridge": "system text blocks to string", "port": 18202}), flush=True)
    uvicorn.run(app, host="127.0.0.1", port=18202, log_level="warning", access_log=False)
