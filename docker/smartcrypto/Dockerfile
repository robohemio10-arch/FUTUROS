FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY smartcrypto ./smartcrypto
COPY scripts ./scripts
COPY config ./config

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e ".[data,ml]"

CMD ["python", "-m", "smartcrypto.bot"]
