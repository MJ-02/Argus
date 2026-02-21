FROM python:3.11-slim

WORKDIR /app

# Install build deps for asyncpg and other C-extensions
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install hatchling first so `pip install .` can use it as the build backend
RUN pip install --no-cache-dir hatchling

# Copy dependency manifest and install Python deps before copying source
# so Docker layer cache is reused when only source files change
COPY backend/pyproject.toml ./pyproject.toml

# Install all project dependencies declared in pyproject.toml.
# Write deps to a temp requirements file to avoid shell quoting issues.
RUN python3 -c "import tomllib; f=open('pyproject.toml','rb'); print('\n'.join(tomllib.load(f)['project']['dependencies']))" > /tmp/requirements.txt && \
    pip install --no-cache-dir -r /tmp/requirements.txt

# Copy the full backend source
COPY backend/ ./

# Add /app to PYTHONPATH so packages (api, crawler, db, workers, shared) are importable
ENV PYTHONPATH=/app
