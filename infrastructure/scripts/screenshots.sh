#!/usr/bin/env bash
# infrastructure/scripts/screenshots.sh — capture the README/demo screenshots against a fresh
# local stack. Runs INSIDE `firebase emulators:exec` (like e2e.sh), so the emulators are up and
# FIRESTORE_EMULATOR_HOST / FIREBASE_AUTH_EMULATOR_HOST are exported. It seeds, starts Django
# (inline) + the Next.js dev server, waits for both, runs the standalone capture script
# (-> docs/img/), and tears everything down.
#
#   PYTHON=<venv>/bin/python firebase emulators:exec --project=demo-benefitservicing-workbench \
#     --config firebase/firebase.json "bash infrastructure/scripts/screenshots.sh"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
export TASK_EXECUTION_MODE="${TASK_EXECUTION_MODE:-inline}"
export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.settings}"
PYTHON="${PYTHON:-python}"

echo "[shots] seeding the emulator…"
"$PYTHON" backend/manage.py seed_demo

echo "[shots] starting Django on :8000…"
"$PYTHON" backend/manage.py runserver 127.0.0.1:8000 --noreload --skip-checks &
DJANGO_PID=$!

echo "[shots] starting Next.js on :3000…"
(
  cd frontend
  export NEXT_PUBLIC_FIREBASE_PROJECT_ID="demo-benefitservicing-workbench"
  export NEXT_PUBLIC_FIREBASE_API_KEY="demo-api-key"
  export NEXT_PUBLIC_USE_FIREBASE_EMULATOR="true"
  export NEXT_PUBLIC_FIREBASE_AUTH_EMULATOR_HOST="http://localhost:9099"
  export NEXT_PUBLIC_FIRESTORE_EMULATOR_HOST="localhost:8080"
  export NEXT_PUBLIC_API_BASE_URL="http://localhost:8000"
  npm run dev
) &
NEXT_PID=$!

cleanup() {
  echo "[shots] stopping Next + Django…"
  kill "${NEXT_PID}" "${DJANGO_PID}" 2>/dev/null || true
  wait "${NEXT_PID}" "${DJANGO_PID}" 2>/dev/null || true
}
trap cleanup EXIT

echo "[shots] waiting for Django + Next to accept connections…"
for i in $(seq 1 120); do
  d="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/health 2>/dev/null || true)"
  n="$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3000 2>/dev/null || true)"
  if [ "${d:-000}" = "200" ] && [ "${n:-000}" = "200" ]; then
    echo "[shots] stack is up (Django ${d}, Next ${n})."
    break
  fi
  if [ "${i}" = "120" ]; then
    echo "[shots] stack did not come up within 120s (Django ${d:-000}, Next ${n:-000})." >&2
    exit 1
  fi
  sleep 1
done

echo "[shots] capturing screenshots -> docs/img/…"
cd frontend
node scripts/capture-screenshots.mjs
echo "[shots] done."
