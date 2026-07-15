#!/usr/bin/env bash
# infrastructure/scripts/lib.sh — shared helpers for the provisioning scripts.
#
# Not run directly: each script `source`s it. It loads infrastructure/config.env (copied from
# config.env.example), validates + derives the knobs, and provides logging + a gcloud URL helper.
set -euo pipefail

INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "${INFRA_DIR}/.." && pwd)"
export INFRA_DIR ROOT

log()  { printf '\033[36m[infra]\033[0m %s\n' "$*"; }
warn() { printf '\033[33m[infra]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31m[infra] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_cmd() { command -v "$1" >/dev/null 2>&1 || die "missing required command: $1"; }

# Load + validate config.
CONFIG_FILE="${CONFIG_FILE:-${INFRA_DIR}/config.env}"
[ -f "${CONFIG_FILE}" ] || die "config not found: ${CONFIG_FILE} — copy config.env.example to config.env and fill it in"
# shellcheck disable=SC1090
source "${CONFIG_FILE}"

: "${PROJECT_ID:?PROJECT_ID must be set in config.env}"
: "${REGION:=us-east4}"
: "${SERVICE_NAME:=bsw-api}"
: "${MIN_INSTANCES:=0}"
: "${MAX_INSTANCES:=2}"
: "${CONCURRENCY:=80}"
: "${CPU:=1}"
: "${MEMORY:=512Mi}"
: "${DJANGO_SECRET_KEY_SECRET:=bsw-django-secret-key}"
: "${CORS_ALLOWED_ORIGINS:=}"

# Derive the service-account emails when not pinned.
: "${RUNTIME_SA:=bsw-api@${PROJECT_ID}.iam.gserviceaccount.com}"
: "${INVOKER_SA:=bsw-invoker@${PROJECT_ID}.iam.gserviceaccount.com}"
export PROJECT_ID REGION SERVICE_NAME MIN_INSTANCES MAX_INSTANCES CONCURRENCY CPU MEMORY
export DJANGO_SECRET_KEY_SECRET CORS_ALLOWED_ORIGINS RUNTIME_SA INVOKER_SA

# Cloud Run's DETERMINISTIC per-service URL: https://<service>-<projectnumber>.<region>.run.app.
# This is the one ALLOWED_HOSTS is set to and that actually serves 200 — `gcloud ... status.url`
# returns a legacy *.run.app alias that 400s against ALLOWED_HOSTS, so we never host-match on it.
# It is derivable BEFORE the first deploy (from the project number), so audience/scheduler wiring
# needs no post-deploy patch.
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT_ID}" --format='value(projectNumber)' 2>/dev/null || true)"
service_host() {
  [ -n "${PROJECT_NUMBER}" ] \
    || die "could not resolve the project number for '${PROJECT_ID}' (gcloud authed? project set?) — needed to derive the deterministic Cloud Run host"
  printf '%s-%s.%s.run.app' "${SERVICE_NAME}" "${PROJECT_NUMBER}" "${REGION}"
}
service_url()  { printf 'https://%s' "$(service_host)"; }
# Back-compat alias: provision-scheduler.sh calls api_url() for the OIDC audience.
api_url() { service_url; }
