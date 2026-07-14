#!/usr/bin/env bash
# infrastructure/scripts/provision-queues.sh — create-or-update the Cloud Tasks queues.
#
# Idempotent (describe -> update|create). The queue set + retry/backoff MIRROR the enqueuer's
# source of truth, backend/internal/enqueue.py::_TASK_SPECS (and specs/21 §21.2). The OIDC token
# and the target URL (/internal/tasks/<task>) are attached PER-TASK by the enqueuer, so the queue
# itself only carries retry/rate. The test-only `noop` queue is intentionally not provisioned.
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
require_cmd gcloud

# name|max_attempts|min_backoff|max_backoff|max_concurrent|dispatches_per_second
# (process-contribution's concurrency 10 + rate 5/s are the only pinned throughput limits —
#  specs/21 §21.2; the rest take generous Cloud Tasks defaults.)
QUEUES=(
  "generate-schedule|5|5s|60s|1000|500"
  "process-contribution|5|10s|300s|10|5"
  "reconcile-contribution|3|30s|30s|1000|500"
  "cancel-future-contributions|5|10s|10s|1000|500"
  "shift-schedule|5|10s|10s|1000|500"
  "propagate-denormalized|3|30s|30s|1000|500"
  "update-projection|3|5s|5s|1000|500"
)

for spec in "${QUEUES[@]}"; do
  IFS='|' read -r name maxa minb maxb conc rate <<<"${spec}"
  if gcloud tasks queues describe "${name}" --location "${REGION}" --project "${PROJECT_ID}" >/dev/null 2>&1; then
    log "queue ${name}: updating"
    verb="update"
  else
    log "queue ${name}: creating"
    verb="create"
  fi
  gcloud tasks queues "${verb}" "${name}" \
    --location "${REGION}" --project "${PROJECT_ID}" \
    --max-attempts "${maxa}" \
    --min-backoff "${minb}" --max-backoff "${maxb}" \
    --max-concurrent-dispatches "${conc}" \
    --max-dispatches-per-second "${rate}"
done

log "queues provisioned (${#QUEUES[@]})."
