FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependency file first for better caching
COPY pyproject.toml README.md ./

# Create empty app directory for install to work
RUN mkdir -p app && touch app/__init__.py

# Install Python dependencies
RUN pip install --no-cache-dir .

# Now copy the actual source code (this layer changes frequently)
# Cache bust: 2026-01-31-v1
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Expose port
EXPOSE 8000

# Set unbuffered output
ENV PYTHONUNBUFFERED=1

# Run migrations (non-blocking) then start the application
CMD ["sh", "-c", "echo '=== RUNNING ALEMBIC MIGRATIONS ===' && (alembic upgrade head || echo '=== MIGRATIONS FAILED, CONTINUING ===' ) && echo '=== STARTING APP ===' && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --log-level info"]
