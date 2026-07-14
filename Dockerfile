# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for pdf/docx parsing and psycopg.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --upgrade pip && pip install ".[dev]"

COPY config ./config
COPY docs ./docs
COPY evals ./evals
# Guardrail files are read from the working directory at runtime.
COPY SYSTEM-PROMPT.md canonicals.yaml ARCHITECTURE.md ./

# Non-root runtime user.
RUN useradd --create-home --uid 10001 stobox && chown -R stobox:stobox /app
USER stobox

# Healthcheck: the package imports cleanly (real health via /health admin cmd).
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import stobox_ai" || exit 1

CMD ["python", "-m", "stobox_ai"]
