#!/usr/bin/env bash
# infrastructure/scripts/provision-all.sh — the full deploy runbook (specs/21 §21.4) as one
# idempotent command. Order matters: IAM -> API (needs the SAs) -> queues -> scheduler (needs the
# live API URL) -> Firestore. Re-runnable; each step reconciles.
#
# The remaining steps need interactive input (bootstrap admin, seed, Vercel), so they are printed
# at the end rather than run here.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log "== 1/5  IAM (service accounts + roles) =="       ; bash "${D}/provision-iam.sh"
log "== 2/5  Cloud Run API (build + deploy) =="        ; bash "${D}/deploy-api.sh"
log "== 3/5  Cloud Tasks queues =="                    ; bash "${D}/provision-queues.sh"
log "== 4/5  Cloud Scheduler jobs =="                  ; bash "${D}/provision-scheduler.sh"
log "== 5/5  Firestore rules/indexes + TTL =="         ; bash "${D}/deploy-firebase.sh"

url="$(api_url)"
cat <<EOF

  ✅ Cloud provisioning complete — ${SERVICE_NAME} @ ${url}

  Remaining manual steps (specs/21 §21.4):
    • Bootstrap the first admin:
        python backend/manage.py set_role admin@demo.test ADMINISTRATOR
    • Seed the demo dataset:
        python backend/manage.py seed_demo --project ${PROJECT_ID}
    • Deploy the frontend to Vercel with the NEXT_PUBLIC_* env (specs/21 §21.3),
      then add the Vercel domain to Firebase Auth > Authorized domains.
    • Alerts (specs/16 §16.4): stuck-PROCESSING > 0 for 30m; any TASK_FAILED; readiness failing 5m.

  Stop the meter when you are done:  bash infrastructure/scripts/teardown.sh
EOF
