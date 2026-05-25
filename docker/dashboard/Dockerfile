FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY smartcrypto ./smartcrypto
COPY config ./config

RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install -e ".[dashboard]"

CMD ["streamlit", "run", "smartcrypto/dashboard/app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
