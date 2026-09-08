# Multi-stage production Dockerfile
FROM python:3.11-slim AS base

# Install system runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    ca-certificates \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency specifications
COPY pyproject.toml README.md ./

# Install python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source
COPY app/ ./app/
COPY dashboard/ ./dashboard/
COPY config/ ./config/

# Create runtime directories
RUN mkdir -p /app/data /app/logs /app/workspaces

# Expose FastAPI dashboard port
EXPOSE 8000

ENV PYTHONUNBUFFERED=1 \
    CONFIG_PATH=/app/config/repositories.yaml \
    WORKSPACE_DIR=/app/workspaces \
    DATA_DIR=/app/data \
    LOG_DIR=/app/logs

# Default entrypoint: start service with web dashboard
CMD ["pr-agent", "start", "--host", "0.0.0.0", "--port", "8000"]
