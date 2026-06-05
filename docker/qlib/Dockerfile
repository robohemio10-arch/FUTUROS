FROM python:3.11-slim

WORKDIR /app

ARG SMARTCRYPTO_UID=10001
ARG SMARTCRYPTO_GID=10001

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

RUN groupadd --system --gid "${SMARTCRYPTO_GID}" smartcrypto \
    && useradd --system --uid "${SMARTCRYPTO_UID}" --gid smartcrypto --home-dir /app --shell /usr/sbin/nologin smartcrypto

COPY docker/qlib/requirements.txt ./docker/qlib/requirements.txt
COPY pyproject.toml README.md ./
COPY smartcrypto ./smartcrypto
COPY scripts ./scripts
COPY config ./config

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r docker/qlib/requirements.txt

RUN mkdir -p /app/data /app/data/reports /app/data/runtime /app/data/features /app/data/predictions /app/logs \
    && chown -R smartcrypto:smartcrypto /app

USER smartcrypto

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m smartcrypto.runtime.container_healthcheck --quiet

CMD ["python", "-m", "smartcrypto.qlib_engine.worker"]
