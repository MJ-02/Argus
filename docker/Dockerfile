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
# --no-build-isolation is required because hatchling is already present
# and we have not copied the full source yet.
RUN pip install --no-cache-dir --no-build-isolation \
      $(python3 -c "import tomllib, sys; with open('pyproject.toml','rb') as f: deps = tomllib.load(f)['project']['dependencies']; print(' '.join(f'\"{d}\"' for d in deps))")

# Copy the full backend source
COPY backend/ ./

# Add /app to PYTHONPATH so packages (api, crawler, db, workers, shared) are importable
ENV PYTHONPATH=/app
