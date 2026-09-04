FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

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
COPY requirements-qlib-security.lock ./
COPY pyproject.toml README.md ./
COPY smartcrypto ./smartcrypto
COPY scripts ./scripts
COPY config ./config

RUN python -m pip install --require-hashes -r requirements-qlib-security.lock \
    && python -m pip install --no-build-isolation --no-deps -e .

RUN mkdir -p /app/data /app/data/reports /app/data/runtime /app/data/features /app/data/predictions /app/logs \
    && chown -R smartcrypto:smartcrypto /app

USER smartcrypto

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m smartcrypto.runtime.container_healthcheck --quiet

CMD ["python", "-m", "smartcrypto.qlib_engine.worker"]
