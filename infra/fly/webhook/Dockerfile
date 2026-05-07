# syntax=docker/dockerfile:1.7
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install system deps for psycopg.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata + source.
COPY pyproject.toml README.md ./
COPY src ./src

# Install only the runtime deps the webhook actually needs. We deliberately
# avoid the full crewai/langgraph/streamlit stack to keep the image small —
# the webhook only touches approval/, models/, notify/, secrets, db/.
RUN pip install --no-cache-dir \
        "fastapi>=0.115.0" \
        "uvicorn[standard]>=0.32.0" \
        "psycopg[binary]>=3.2.0" \
        "pydantic>=2.9.0" \
        "boto3>=1.35.0" \
        "rich>=13.9.0" \
        "pyyaml>=6.0.2"

# Install the package itself (no deps; we already pinned them above).
RUN pip install --no-cache-dir --no-deps -e .

EXPOSE 8080

CMD ["uvicorn", "minions.webhook.app:app", "--host", "0.0.0.0", "--port", "8080"]
