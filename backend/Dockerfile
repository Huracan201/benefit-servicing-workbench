# syntax=docker/dockerfile:1
# Cloud Run image for the BenefitServicing Workbench command API (specs/21 §21.2).
#
# One Django/gunicorn process serves BOTH surfaces — /api/v1 (Firebase-token auth) and
# /internal (OIDC; Cloud Tasks + Scheduler). Django is the auth boundary for both
# (specs/12 §12.5), so the Cloud Run service runs --allow-unauthenticated.
#
# Build context is backend/ (this dir):  docker build -t bsw-api backend/
#                                         gcloud builds submit backend/ --tag <image>
#
# No JSON key files are ever baked in: Cloud Run uses ADC (the runtime service account),
# local dev uses the emulator (no credentials). Python 3.12 is pinned per specs/21 §21.5.
FROM python:3.12-slim

# PYTHONUNBUFFERED    — stream logs to Cloud Logging unbuffered.
# PYTHONDONTWRITEBYTECODE — no .pyc written into the image layer.
# PORT                — Cloud Run overrides this at runtime; 8080 is the local default.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Install dependencies in their own layer so app-code changes don't re-run pip.
COPY requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY . ./

# Run as a non-root user (defense-in-depth — the container needs no root capability).
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Cloud Run terminates TLS and probes GET /health + /readiness (both unauthenticated); it
# routes traffic to $PORT. gunicorn binds all interfaces INSIDE the container. Workers/threads
# are sized for 1 vCPU / 512 MiB at concurrency 80 (specs/21 §21.2): 2 workers x 4 threads.
# --timeout 120 comfortably covers the two-phase payment adapter round-trip (~2.5 min ceiling
# lives in the async path, not a sync request). `exec` makes gunicorn PID 1 for clean SIGTERM.
EXPOSE 8080
CMD exec gunicorn config.wsgi:application \
    --bind "0.0.0.0:${PORT}" \
    --workers 2 \
    --threads 4 \
    --timeout 120 \
    --access-logfile - \
    --error-logfile -
