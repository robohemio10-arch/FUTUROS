FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONPATH=/app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        git \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY docker/qlib/requirements.txt ./docker/qlib/requirements.txt
COPY pyproject.toml README.md ./
COPY smartcrypto ./smartcrypto
COPY scripts ./scripts
COPY config ./config

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r docker/qlib/requirements.txt

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m smartcrypto.runtime.container_healthcheck --quiet

CMD ["python", "-m", "smartcrypto.qlib_engine.worker"]
