# All tools live together; Cortex/Evolve APIs stay on container loopback.
# CORTEX_SOURCE=local builds a Cortex checkout passed as the cortex-checkout build context.
ARG CORTEX_SOURCE=pinned
FROM golang:1.26-alpine@sha256:9ce231b28998b32e116927c61aa5c406183061d4f3f2d61b292c88e7f6bdf7ca AS cortex-pinned
RUN apk add --no-cache curl git
ARG CORTEX_REV=4262d0a84c37589e8d7907cfb70c46f6e08694d4
WORKDIR /src
RUN curl -fsSL "https://codeload.github.com/vinodmut/cortex/tar.gz/${CORTEX_REV}" \
    | tar -xz --strip-components=1

FROM golang:1.26-alpine@sha256:9ce231b28998b32e116927c61aa5c406183061d4f3f2d61b292c88e7f6bdf7ca AS cortex-local
RUN apk add --no-cache git
WORKDIR /src
COPY --from=cortex-checkout . .

FROM cortex-${CORTEX_SOURCE} AS cortex-build
RUN tags="$(go -C scripts/profile-tags run . full)" \
    && cd cmd/cortex \
    && GOWORK=off CGO_ENABLED=0 go build -tags "$tags" -ldflags="-s -w" -o /cortex .

FROM ghcr.io/astral-sh/uv:0.9.5 AS uv
FROM python:3.12.8-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash ca-certificates curl git jq libimage-exiftool-perl openssl ripgrep \
    && rm -rf /var/lib/apt/lists/*
COPY --from=uv /uv /usr/local/bin/uv
COPY --from=cortex-build /cortex /usr/local/bin/cortex
RUN useradd --create-home --uid 1000 demo \
    && mkdir /opt/demo /results /opt/embeddings \
    && chown -R demo:demo /opt/demo /results /opt/embeddings
USER demo
ARG CLAUDE_VERSION=2.1.287
RUN curl -fsSL -o /tmp/install-claude.sh https://claude.ai/install.sh \
    && bash /tmp/install-claude.sh "$CLAUDE_VERSION" \
    && rm /tmp/install-claude.sh
ENV PATH="/home/demo/.local/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/opt/embeddings \
    EVOLVE_EMBEDDING_PROVIDER=fastembed
WORKDIR /opt/demo
COPY --chown=demo:demo pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev \
    && uv run --offline --no-sync python -c 'from altk_evolve.embeddings import get_embedding_model, MINILM_MODEL; get_embedding_model(MINILM_MODEL).encode("photo metadata")'
COPY --chown=demo:demo demo.py services.py report.py sample.jpg ./
COPY --chown=demo:demo prompts/ prompts/
RUN claude --version && exiftool -ver \
    && printf '%s\n' "$CLAUDE_VERSION" > /opt/demo/claude-version.txt
CMD ["uv", "run", "--offline", "--no-sync", "python", "services.py", "serve"]
