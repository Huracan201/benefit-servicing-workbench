#!/usr/bin/env bash
# infrastructure/scripts/deploy-firebase.sh — deploy Firestore rules + indexes from source and
# enable the idempotency-key TTL policy (specs/21 §21.4 steps 2–3).
#
# Rules + indexes are the SOURCE OF TRUTH (firebase/) — never edited in the console. The TTL
# policy is the easy-to-miss step: without it, completed idempotency keys never expire.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud

# firebase-tools is used globally or via npx (repo convention). Override with FIREBASE_BIN.
FIREBASE_BIN="${FIREBASE_BIN:-firebase}"
command -v "${FIREBASE_BIN}" >/dev/null 2>&1 \
  || die "firebase CLI not found — 'npm i -g firebase-tools' or set FIREBASE_BIN='npx firebase-tools'"

log "deploying Firestore rules + indexes to ${PROJECT_ID}…"
# --force auto-confirms the "delete these indexes?" prompt; --non-interactive fails fast instead
# of HANGING on a prompt when there is no TTY (this session / CI / any non-interactive runner).
"${FIREBASE_BIN}" deploy --only firestore:rules,firestore:indexes \
  --config "${ROOT}/firebase/firebase.json" --project "${PROJECT_ID}" --force --non-interactive

log "enabling the idempotencyKeys TTL policy (field: expiresAt)…"
gcloud firestore fields ttls update expiresAt \
  --collection-group=idempotencyKeys --enable-ttl \
  --project "${PROJECT_ID}"

log "Firestore rules/indexes deployed + TTL enabled."
