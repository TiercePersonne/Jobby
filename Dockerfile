# Jobby - Autonomous Job & Post Hunter Agent (Production Dockerfile)
FROM python:3.11-slim

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

# Pre-install dependencies to maximize Docker layer cache
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir \
        "beautifulsoup4>=4.15.0" \
        "fastapi>=0.162.4" \
        "httpx>=0.28.1" \
        "jinja2>=3.1.6" \
        "pillow>=13.0.0" \
        "playwright>=1.49.0" \
        "pydantic>=2.14.3" \
        "pypdf>=5.0.0" \
        "python-dateutil>=4.0.0" \
        "python-multipart>=0.0.18" \
        "pyyaml>=6.0.3" \
        "rich>=15.0.0" \
        "typer>=0.29.0" \
        "uvicorn[standard]>=0.54.0"

# Install Playwright Chromium with all needed system dependencies
RUN playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

# Copy application source code (fast layer for updates)
COPY src/ ./src/
RUN pip install --no-cache-dir --no-deps -e .

# Create persistent storage mount directory
RUN mkdir -p /data

EXPOSE 8000

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/manifest.json || exit 1

# Start Jobby web server
CMD ["uvicorn", "jobby.server:app", "--host", "0.0.0.0", "--port", "8000"]
