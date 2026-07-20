FROM python:3.11-slim@sha256:e031123e3d85762b141ad1cbc56452ba69c6e722ebf2f042cc0dc86c47c0d8b3

# System dependencies: ffmpeg (video processing) + OpenCV libs
RUN apt-get update && apt-get install -y --no-install-recommends --fix-missing \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (leverage Docker layer cache)
COPY requirements.txt .
RUN pip install --no-cache-dir --retries 5 --timeout 120 --trusted-host pypi.org --trusted-host files.pythonhosted.org -r requirements.txt

# Copy the core pipeline
COPY genai-pipeline/ ./genai-pipeline/

# Copy the web app
COPY web_app/ ./web_app/

WORKDIR /app

# Force unbuffered stdout/stderr so Docker logs appear in real-time
ENV PYTHONUNBUFFERED=1
ENV PYTHONIOENCODING=utf-8

# ---- Logging configuration -------------------------------------------
# LOG_LEVEL: minimum log level (DEBUG, INFO, WARNING, ERROR)
# In Docker, console output goes to stdout (captured by Docker's logging driver)
# File logs are written to the output/ directory (mount as volume to persist)
ENV LOG_LEVEL=INFO
ENV LOG_FORMAT=json

# ---- Web server configuration ----------------------------------------
ENV WEB_HOST=0.0.0.0
ENV WEB_PORT=5000
ENV WEB_DEBUG=0

# Output directory (must match the volume mount in docker-compose.yml)
ENV OUTPUT_DIR=/app/genai-pipeline/output

# ---- Expose port -----------------------------------------------------
EXPOSE 5000

# ---- Pre-start DB migration → Flask web service --------------------------
# 1. migrate_db.py initialises the DB and runs Alembic ONCE with a single
#    engine, before any background threads touch the SQLite file.
# 2. Then Flask starts — Gateway will see the already-initialised engine and
#    skip init_db entirely (idempotent).
CMD sh -c "python genai-pipeline/migrate_db.py && exec python -m flask --app web_app/app.py run --host=0.0.0.0 --port=5000"
