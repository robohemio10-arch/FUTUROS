FROM python:3.11-slim

WORKDIR /app

ARG SMARTCRYPTO_UID=10001
ARG SMARTCRYPTO_GID=10001

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system --gid "${SMARTCRYPTO_GID}" smartcrypto \
    && useradd --system --uid "${SMARTCRYPTO_UID}" --gid smartcrypto --home-dir /app --shell /usr/sbin/nologin smartcrypto

COPY pyproject.toml README.md ./
COPY smartcrypto ./smartcrypto
COPY config ./config

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e ".[dashboard]"

RUN mkdir -p /app/data /app/data/reports /app/data/runtime /app/logs \
    && chown -R smartcrypto:smartcrypto /app

USER smartcrypto

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m smartcrypto.runtime.container_healthcheck --quiet

CMD ["streamlit", "run", "smartcrypto/dashboard/app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
