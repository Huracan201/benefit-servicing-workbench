"""``python manage.py run_job <name>`` — fire a scheduler job on demand (21 §21.5).

The local-dev / demo counterpart to Cloud Scheduler: with the emulator running
and ``TASK_EXECUTION_MODE=inline``, this invokes a registered scheduler job
synchronously (which enqueues its tasks inline, so the whole async surface runs
in-process). Also used to demo due-processing without waiting for the cron tick.

    python manage.py run_job noop
    python manage.py run_job enqueue-due-contributions --payload '{"limit": 50}'

Jobs are looked up in :data:`internal.enqueue.SCHEDULER_JOBS`; an unknown name
lists the registered jobs and exits non-zero.
"""

from __future__ import annotations

import json

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Fire a registered Cloud Scheduler job by name (specs/21 §21.5)."

    def add_arguments(self, parser):
        parser.add_argument("name", help="Registered scheduler job name.")
        parser.add_argument(
            "--payload", default=None,
            help="Optional JSON object passed to the job.",
        )

    def handle(self, *args, **options):
        from internal.enqueue import SCHEDULER_JOBS
        from internal.system_context import system_ctx

        name = options["name"]
        job = SCHEDULER_JOBS.get(name)
        if job is None:
            raise CommandError(
                f"Unknown job '{name}'. Registered jobs: "
                f"{sorted(SCHEDULER_JOBS) or '(none)'}"
            )

        payload = {}
        if options["payload"]:
            try:
                payload = json.loads(options["payload"])
            except json.JSONDecodeError as exc:
                raise CommandError(f"--payload is not valid JSON: {exc}")
            if not isinstance(payload, dict):
                raise CommandError("--payload must be a JSON object.")

        ctx = system_ctx(name)
        self.stdout.write(f"Running job '{name}' (correlation {ctx.correlation_id})...")
        result = job(payload, ctx)
        self.stdout.write(self.style.SUCCESS(f"Job '{name}' -> {result}"))
