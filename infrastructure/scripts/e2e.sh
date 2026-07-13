#!/usr/bin/env bash
# infrastructure/scripts/e2e.sh — critical-path E2E harness (specs/17 §17.4).
#
# Invoked by the CI `e2e` job INSIDE `firebase emulators:exec` (.github/workflows/ci.yml),
# so the Firestore + Auth emulators are already running and FIRESTORE_EMULATOR_HOST /
# FIREBASE_AUTH_EMULATOR_HOST are exported into this process. It then:
#   1. seeds the emulator (seed_demo — the deterministic dataset + the three demo users),
#   2. starts the Django command API on :8000 in INLINE task mode (an inline follow-up task
#      mirrors the cloud handler exactly — backend/internal/enqueue.py),
#   3. runs the Playwright critical-path specs; Playwright's own webServer starts Next.js on
#      :3000 with the emulator-wired NEXT_PUBLIC_* env (frontend/playwright.config.ts),
#   4. tears Django down on exit (pass or fail).
#
# Prereqs, installed by the CI job before this runs: backend deps (pip), frontend deps
# (npm ci) + the Chromium browser (npx playwright install --with-deps chromium), and
# firebase-tools (which provides the emulators:exec wrapper).
#
# Local use: from a shell that already has the emulator running and the deps installed,
#   firebase emulators:exec --project=demo-benefitservicing-workbench \
#     --config firebase/firebase.json "bash infrastructure/scripts/e2e.sh"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Inline execution is the emulator default (FIRESTORE_EMULATOR_HOST is set), but pin it so the
# harness is explicit and independent of that inference.
export TASK_EXECUTION_MODE="${TASK_EXECUTION_MODE:-inline}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"

echo "[e2e] seeding the emulator (seed_demo)…"
python backend/manage.py seed_demo

echo "[e2e] starting the Django command API on 127.0.0.1:8000…"
python backend/manage.py runserver 127.0.0.1:8000 --noreload --skip-checks &
DJANGO_PID=$!

cleanup() {
  echo "[e2e] stopping Django (pid ${DJANGO_PID})…"
  kill "${DJANGO_PID}" 2>/dev/null || true
  wait "${DJANGO_PID}" 2>/dev/null || true
}
trap cleanup EXIT

echo "[e2e] waiting for the command API to accept connections…"
for i in $(seq 1 60); do
  # Any HTTP response (even a 404) proves Django's routes are loaded and it is accepting
  # connections; "000" is curl's code for "no connection yet".
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/readiness" || echo 000)"
  if [ "${code}" != "000" ]; then
    echo "[e2e] command API is up (HTTP ${code})."
    break
  fi
  if ! kill -0 "${DJANGO_PID}" 2>/dev/null; then
    echo "[e2e] Django exited during startup." >&2
    exit 1
  fi
  if [ "${i}" = "60" ]; then
    echo "[e2e] Django did not accept connections within 60s." >&2
    exit 1
  fi
  sleep 1
done

echo "[e2e] running Playwright critical paths…"
cd "${ROOT}/frontend"
npx playwright test
