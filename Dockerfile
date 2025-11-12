# Backend Dockerfile for FreelanceHunt API
# Port: 7300

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create logs directory
RUN mkdir -p logs

# Expose port 7300
EXPOSE 7300

# Health check (using curl instead of requests)
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:7300/health || exit 1

# Run the application with reload enabled
# --reload-dir specifies which directories to watch for changes
# This is important when using volume mounts in docker-compose
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "7300", "--reload", "--reload-dir", "/app"]
