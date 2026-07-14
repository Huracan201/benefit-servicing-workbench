#!/usr/bin/env bash
# infrastructure/scripts/demo-up.sh — one-command local demo of the FULL stack (specs/21 §21.5).
#
# Runs INSIDE `firebase emulators:exec` (see the repo-root Makefile `demo` target), so the
# Firestore + Auth emulators are already up and FIRESTORE_EMULATOR_HOST /
# FIREBASE_AUTH_EMULATOR_HOST are exported into this process. It then:
#   1. seeds the emulator (seed_demo — the deterministic dataset + the three demo users),
#   2. starts the Django command API on :8000 in INLINE task mode (the async surface runs
#      synchronously in-process — backend/internal/enqueue.py mirrors the cloud handlers),
#   3. starts the Next.js workbench on :3000 in the FOREGROUND, wired to the emulator with the
#      same NEXT_PUBLIC_* env the Playwright harness uses (frontend/playwright.config.ts).
#
# Ctrl-C stops Next.js -> the trap stops Django -> emulators:exec stops the emulator. One key
# up, one key down, zero cloud cost.
#
# Prereqs (identical to the local dev loop — specs/21 §21.5): Python 3.12 with backend deps
# installed, Node 20 with frontend deps installed (npm ci), Java 21 + firebase-tools for the
# emulator. See firebase/emulator/README.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Inline execution is the emulator default (FIRESTORE_EMULATOR_HOST is set), but pin it so the
# harness is explicit and independent of that inference.
export TASK_EXECUTION_MODE="${TASK_EXECUTION_MODE:-inline}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
PYTHON="${PYTHON:-python}"

echo "[demo] seeding the emulator (seed_demo — deterministic dataset + 3 demo users)…"
"$PYTHON" backend/manage.py seed_demo

echo "[demo] starting the Django command API on 127.0.0.1:8000 (inline task mode)…"
"$PYTHON" backend/manage.py runserver 127.0.0.1:8000 --noreload --skip-checks &
DJANGO_PID=$!

cleanup() {
  echo "[demo] stopping Django (pid ${DJANGO_PID})…"
  kill "${DJANGO_PID}" 2>/dev/null || true
  wait "${DJANGO_PID}" 2>/dev/null || true
}
trap cleanup EXIT

echo "[demo] waiting for the command API to accept connections…"
for i in $(seq 1 60); do
  # Poll LIVENESS: /health is 200 the moment Django serves (unlike /readiness, which 503s until
  # Firestore is reachable — a false negative here). curl writes "000" AND exits non-zero on a
  # refused connection, so `|| true` + an empty-capture default keep a failure exactly "000".
  code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/health" 2>/dev/null || true)"
  code="${code:-000}"
  if [ "${code}" = "200" ]; then
    echo "[demo] command API is up (HTTP 200)."
    break
  fi
  if ! kill -0 "${DJANGO_PID}" 2>/dev/null; then
    echo "[demo] Django exited during startup." >&2
    exit 1
  fi
  if [ "${i}" = "60" ]; then
    echo "[demo] Django did not accept connections within 60s." >&2
    exit 1
  fi
  sleep 1
done

cat <<'BANNER'

  ┌──────────────────────────────────────────────────────────────────┐
  │  BenefitServicing Workbench — local demo is UP                     │
  │                                                                    │
  │    Workbench UI   ->  http://localhost:3000   <- start here        │
  │    Command API    ->  http://localhost:8000/api/v1                 │
  │    Emulators      ->  Firestore :8080 · Auth :9099 (seeded)        │
  │                                                                    │
  │  Sign in with a seeded demo user (creds in docs/demo-script.md).   │
  │  Ctrl-C tears the whole stack down.                                │
  └──────────────────────────────────────────────────────────────────┘

BANNER

echo "[demo] starting the Next.js workbench on :3000 (foreground — Ctrl-C stops everything)…"
cd "${ROOT}/frontend"
# Wire the browser SDK to the emulator with the SAME known-good env as the Playwright harness
# (frontend/playwright.config.ts). These are demo-only literals — no real Firebase project and
# no secrets (the emulator ignores the api key; it exists only to satisfy the SDK constructor).
export NEXT_PUBLIC_FIREBASE_PROJECT_ID="demo-benefitservicing-workbench"
export NEXT_PUBLIC_FIREBASE_API_KEY="demo-api-key"
export NEXT_PUBLIC_USE_FIREBASE_EMULATOR="true"
export NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_HOST="http://localhost:9099"
export NEXT_PUBLIC_FIRESTORE_EMULATOR_HOST="localhost:8080"
export NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"
npm run dev
