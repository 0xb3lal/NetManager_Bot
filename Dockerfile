# syntax=docker/dockerfile:1
# Use a slim Python base image for a smaller footprint
FROM python:3.13.7-slim

# Set UTF-8 locale and Python runtime options
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Set the working directory inside the container
WORKDIR /app

# Install system dependencies (kept minimal) and clean up in the same layer
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker layer caching
COPY requirements.txt /app/requirements.txt

# Install Python dependencies without cache to reduce image size
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the rest of the application code
COPY . /app

# Optional health check: ensure the main process is alive
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, sys; sys.exit(0 if os.path.exists('bot.py') else 1)"

# Run the bot
CMD ["python", "bot.py"]
