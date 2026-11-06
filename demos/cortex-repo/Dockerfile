# The launcher first builds the shared, pinned Cortex/Evolve/Claude runtime.
FROM cortex-invoice-runtime:local
USER root
# The sample repository's own toolchain: system Python with its locked test dependencies.
RUN apt-get update && apt-get install -y --no-install-recommends make sqlite3 \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir pyyaml==6.0.3 pytest==8.4.2
USER demo
RUN git config --global user.email agent@shopkit.test \
    && git config --global user.name "shopkit agent" \
    && git config --global init.defaultBranch main \
    && git config --global --add safe.directory '*'
COPY --chown=demo:demo driver.py grade.py summary.py tasks.json ./
COPY --chown=demo:demo shopkit/ shopkit/
RUN python3 -c "import yaml, pytest" && make --version && claude --version
