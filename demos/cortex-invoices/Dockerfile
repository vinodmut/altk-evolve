# The launcher first builds the shared, pinned Cortex/Evolve/Claude runtime.
FROM cortex-invoice-runtime:local
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*
USER demo
COPY --chown=demo:demo driver.py placement.py prompt.txt ./
COPY --chown=demo:demo fixtures/ fixtures/
RUN pdftotext -v && tesseract --version && claude --version
