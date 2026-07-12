"""Pure unit tests (``@tag('unit')``) for the Phase-3 /internal wiring (unit B).

Offline, database-free (``SimpleTestCase`` + ``databases = []``): they pin the
seams the async command layer depends on, without an emulator —

* every CONTRACT task + job name is registered at ``internal.enqueue`` import;
* ``enqueue`` returns the inline handler's result dict (so a command renders its
  completed 200) and ``None`` in cloud mode (so a command answers 202) — the
  completion-protocol step-3 seam;
* the ``name`` de-dup argument is threaded to the cloud CreateTask path;
* the task-adapter helpers classify retryable-vs-terminal and no-op a keyless
  completion.

Firestore-touching adapter bodies are exercised by the emulator suite
(``test_wiring_emulator.py``); here we only touch the pure seams.
"""

from __future__ import annotations

from unittest import mock

from django.test import SimpleTestCase, override_settings, tag

from commands.base import CommandContext
from internal import enqueue, jobs, tasks
from internal.enqueue import SCHEDULER_JOBS, TASK_HANDLERS, register_task


def _origin_ctx() -> CommandContext:
    """A request-derived (non-SYSTEM) enqueuing context."""
    return CommandContext.build(
        actor_id="user-1", actor_role="SERVICING_MANAGER", actor_name="Op",
        method="POST", path="/api/v1/x", body=None, idempotency_key="k1",
    )


@tag("unit")
class RegistrationTests(SimpleTestCase):
    databases: list[str] = []

    def test_every_contract_task_is_registered(self):
        for name, handler in (
            ("generate-schedule", tasks.generate_schedule_task),
            ("process-contribution", tasks.process_contribution_task),
            ("reconcile-contribution", tasks.reconcile_contribution_task),
            ("cancel-future-contributions", tasks.cancel_future_contributions_task),
            ("shift-schedule", tasks.shift_schedule_task),
        ):
            with self.subTest(task=name):
                self.assertIs(TASK_HANDLERS.get(name), handler)

    def test_every_contract_job_is_registered(self):
        for name, handler in (
            ("enqueue-due-contributions", jobs.enqueue_due_contributions),
            ("reconcile-stuck-payments", jobs.reconcile_stuck_payments),
            ("reap-expired-leases", jobs.reap_expired_leases_job),
        ):
            with self.subTest(job=name):
                self.assertIs(SCHEDULER_JOBS.get(name), handler)

    def test_every_registered_task_has_queue_config(self):
        # A cloud enqueue of a registered task must find a queue (else it raises).
        from internal.enqueue import QUEUE_CONFIG

        for name in ("generate-schedule", "process-contribution",
                     "reconcile-contribution", "cancel-future-contributions",
                     "shift-schedule"):
            with self.subTest(task=name):
                self.assertIn(name, QUEUE_CONFIG)


@tag("unit")
class EnqueueReturnValueTests(SimpleTestCase):
    databases: list[str] = []

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_inline_returns_handler_result(self):
        register_task("wire-spy", lambda payload, ctx: {"ok": True, "echo": payload})
        self.addCleanup(TASK_HANDLERS.pop, "wire-spy", None)
        result = enqueue.enqueue("wire-spy", {"x": 9}, ctx=_origin_ctx())
        self.assertEqual(result, {"ok": True, "echo": {"x": 9}})

    @override_settings(TASK_EXECUTION_MODE="cloud")
    def test_cloud_returns_none(self):
        # Cloud mode only schedules — the command answers 202. Patch the cloud
        # sender so the test needs no google-cloud-tasks / credentials.
        with mock.patch.object(enqueue, "_enqueue_cloud") as sender:
            result = enqueue.enqueue(
                "process-contribution", {"contributionId": "c1"}, ctx=_origin_ctx()
            )
        self.assertIsNone(result)
        sender.assert_called_once()

    @override_settings(TASK_EXECUTION_MODE="cloud")
    def test_name_threaded_to_cloud_for_dedup(self):
        with mock.patch.object(enqueue, "_enqueue_cloud") as sender:
            enqueue.enqueue(
                "reconcile-contribution", {"contributionId": "c1"},
                ctx=_origin_ctx(), name="reconcile-c1-42",
            )
        self.assertEqual(sender.call_args.kwargs.get("name"), "reconcile-c1-42")


@tag("unit")
class EnqueueDueDeferredTests(SimpleTestCase):
    databases: list[str] = []

    def test_page_cap_logs_deferred_never_silently_capped(self):
        # Force every page to be "full with more behind the cursor" and cap at one
        # page, so the run must report deferred work (specs/14 §14.6) — mocked so it
        # needs no Firestore.
        full_page = ([{"id": "c1", "benefitAgreementId": "a1"}], object())  # non-None cursor

        with mock.patch("common.firestore.get_client", return_value=object()), \
             mock.patch("repositories.contributions.due", return_value=full_page), \
             mock.patch("repositories.agreements.get",
                        return_value={"acceptingPayments": True}), \
             mock.patch("internal.enqueue.enqueue") as spy, \
             mock.patch.object(jobs, "_MAX_PAGES", 1), \
             self.assertLogs("bsw.internal", level="WARNING") as logs:
            summary = jobs.enqueue_due_contributions({}, _origin_ctx())

        self.assertTrue(summary["deferred"])
        self.assertGreaterEqual(spy.call_count, 1)  # the eligible item was enqueued
        self.assertTrue(any("deferred" in m for m in logs.output))


@tag("unit")
class TaskHelperTests(SimpleTestCase):
    databases: list[str] = []

    def test_is_retryable_transient_true_terminal_false(self):
        from commands.base import OperationInProgress, StaleWrite, Unprocessable

        self.assertTrue(tasks._is_retryable(OperationInProgress()))
        self.assertTrue(tasks._is_retryable(StaleWrite("changed under us")))
        self.assertFalse(tasks._is_retryable(Unprocessable("bad input")))

    def test_complete_key_noop_without_key(self):
        # No key (a reaper re-drive) is a no-op that never touches the client.
        sentinel_client = object()
        self.assertIsNone(tasks._complete_key(sentinel_client, None, {"any": 1}))
