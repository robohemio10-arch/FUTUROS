FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

ARG BITRADEX_UID=10001
ARG BITRADEX_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/src \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    fonts-liberation \
    libasound2 \
    libatk-bridge2.0-0 \
    libatk1.0-0 \
    libcairo2 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libglib2.0-0 \
    libgtk-3-0 \
    libnspr4 \
    libnss3 \
    libpango-1.0-0 \
    libx11-6 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxkbcommon0 \
    libxrandr2 \
    wget \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid "${BITRADEX_GID}" bitradex \
    && useradd --system --uid "${BITRADEX_UID}" --gid bitradex --home-dir /app --shell /usr/sbin/nologin bitradex

COPY requirements.txt /app/requirements.txt
RUN python -m pip install --require-hashes -r /app/requirements.txt && python -m playwright install chromium

COPY src /app/src
COPY README_RUNBOOK.md /app/README_RUNBOOK.md

RUN mkdir -p /app/data/raw /app/data/output /app/data/runtime /app/logs \
    && chown -R bitradex:bitradex /app /ms-playwright

USER bitradex

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import bitradex_realtime_collector; print('bitradex collector ok')"

CMD ["python", "-m", "bitradex_realtime_collector.main", "--mode", "daemon", "--capture-seconds", "0", "--scroll-rounds", "20", "--export-every-seconds", "60"]
