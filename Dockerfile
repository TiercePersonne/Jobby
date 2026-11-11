# Jobby - Autonomous Job & Post Hunter Agent (Production Dockerfile)
FROM python:3.11-slim

# Set timezone and noninteractive frontend
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    JOBBY_DATABASE_PATH=/data/jobby.db \
    JOBBY_CONFIG_PATH=/data/jobby_config.yaml

WORKDIR /app

# Install system utilities & SSL certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    libatomic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency configuration and source
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install python dependencies and project
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .

# Install Playwright Chromium with all needed system dependencies
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Create persistent storage mount directory
RUN mkdir -p /data

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/manifest.json || exit 1

# Start Jobby web server
CMD ["uvicorn", "jobby.server:app", "--host", "0.0.0.0", "--port", "8000"]
