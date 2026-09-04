FROM python:3.11-slim@sha256:9534e5a8e315485d4061ed659af0fd78a284c015f9b73661b41d6bab25604534

WORKDIR /app

ARG SMARTCRYPTO_UID=10001
ARG SMARTCRYPTO_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid "${SMARTCRYPTO_GID}" smartcrypto \
    && useradd --system --uid "${SMARTCRYPTO_UID}" --gid smartcrypto --home-dir /app --shell /usr/sbin/nologin smartcrypto

COPY pyproject.toml README.md requirements-runtime.lock ./
COPY smartcrypto ./smartcrypto
COPY scripts ./scripts
COPY config ./config

RUN python -m pip install --require-hashes -r requirements-runtime.lock \
    && python -m pip install --no-build-isolation --no-deps -e .

RUN mkdir -p /app/data /app/data/reports /app/data/runtime /app/data/features /app/data/predictions /app/data/sqlite /app/logs \
    && chown -R smartcrypto:smartcrypto /app

USER smartcrypto

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m smartcrypto.runtime.container_healthcheck --quiet

CMD ["python", "-m", "smartcrypto.bot"]
