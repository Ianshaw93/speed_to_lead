FROM python:3.11-slim

# Force rebuild: 2026-01-30-v3
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy all source files needed for install
COPY pyproject.toml README.md ./
COPY app/ app/

# Install Python dependencies
RUN pip install --no-cache-dir .

# Copy alembic files
COPY alembic/ alembic/
COPY alembic.ini .

# Expose port
EXPOSE 8000

# Run the application
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
