# syntax=docker/dockerfile:1
# Multi-stage: build wheels with the toolchain, ship a slim runtime without it.

FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1
WORKDIR /app

# Toolchain only exists in this stage — never in the runtime image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /venv && /venv/bin/pip install --upgrade pip

# Dependency layer from the LOCK (reproducible builds) — cached until
# requirements.lock changes, so code edits don't reinstall the world.
COPY requirements.lock ./
RUN /venv/bin/pip install -r requirements.lock

# App layer: the package itself, deps already satisfied above.
COPY pyproject.toml README.md ./
COPY src ./src
RUN /venv/bin/pip install --no-deps .


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/venv/bin:$PATH"

WORKDIR /app

# Runtime shared libs only.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /venv /venv

COPY config ./config
COPY docs ./docs
COPY evals ./evals
# Guardrail files are read from the working directory at runtime.
COPY SYSTEM-PROMPT.md canonicals.yaml ARCHITECTURE.md ./

# Non-root runtime user.
RUN useradd --create-home --uid 10001 stobox && chown -R stobox:stobox /app
USER stobox

# Real liveness: the job queue touches HEARTBEAT_FILE every 60s (proactive.py).
# Stale mtime = wedged event loop / dead polling — an `import` check can't see
# that. start-period covers boot indexing.
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
    CMD python -c "import os,sys,time; p=os.environ.get('HEARTBEAT_FILE','/tmp/stobox-heartbeat'); sys.exit(0 if os.path.exists(p) and time.time()-os.path.getmtime(p)<180 else 1)"

CMD ["python", "-m", "stobox_ai"]
