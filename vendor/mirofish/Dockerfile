# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# build-essential covers any source-built wheels; curl for debugging/health.
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install CPU-only torch FIRST. torch is a deep transitive dep of camel-ai with
# no exact pin; on Linux the default wheel is the ~2.7GB CUDA build, which we
# neither need (agents call the LLM API, not local models) nor can afford in RAM.
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt --extra-index-url https://download.pytorch.org/whl/cpu

COPY . .

# Prod data lives on a mounted disk (see ../render.yaml). These defaults also let
# a bare `docker run` work. Overridden by render.yaml envVars in the real deploy.
ENV DATA_DIR=/var/data \
    UPLOAD_FOLDER=/var/data/uploads \
    OASIS_SIMULATION_DATA_DIR=/var/data/uploads/simulations \
    FLASK_DEBUG=false
RUN mkdir -p /var/data/uploads/simulations /var/data/briefs_v2

# Render injects $PORT. One worker (the task manager keeps in-memory state, so
# multiple workers would fragment it) + threads for concurrent polling; timeout 0
# so the ~3-min synchronous brief build and multi-minute sims aren't killed.
CMD ["sh", "-c", "gunicorn 'app:create_app()' --workers 1 --worker-class gthread --threads 8 --timeout 0 --bind 0.0.0.0:${PORT:-10000}"]
