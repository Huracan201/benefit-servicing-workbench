"""Pure unit tests (``@tag('unit')``) for the async infrastructure foundation.

Offline, database-free (``SimpleTestCase`` + ``databases = []``): they pin the
seams U1 owns without an emulator —

* ``system_ctx`` mints the un-forgeable SYSTEM marker + namespaced correlation;
* ``CommandContext`` is non-SYSTEM by default and via ``build`` (existing callers);
* ``enqueue`` inline runs the SAME callable with a fresh SYSTEM ctx (the CI↔prod
  mirror seam), and cloud mode fails clearly when unconfigured/absent;
* ``dead_letter.is_final_attempt`` reads the Cloud Tasks retry header vs
  ``max_attempts`` (absent ⇒ not final), and ``task_response`` maps retryable →
  5xx / terminal (and exhausted retryable) → 2xx;
* the base ``/internal/tasks|jobs`` views run the callable and render the envelope.

Firestore-touching paths (``_record_task_failed``) are best-effort and swallow the
absent-``google.cloud`` ImportError, so these run with no client.
"""

from __future__ import annotations

import json

from django.test import RequestFactory, SimpleTestCase, override_settings, tag

from commands.base import CommandContext
from internal import dead_letter, enqueue, views
from internal.enqueue import DEFAULT_MAX_ATTEMPTS, QUEUE_CONFIG, register_task
from internal.system_context import system_ctx


def _origin_ctx() -> CommandContext:
    """A request-derived (non-SYSTEM) enqueuing context."""
    return CommandContext.build(
        actor_id="user-1", actor_role="SERVICING_MANAGER", actor_name="Op",
        method="POST", path="/api/v1/x", body=None, idempotency_key="k1",
    )


@tag("unit")
class SystemContextTests(SimpleTestCase):
    databases: list[str] = []

    def test_marks_system_and_actor(self):
        ctx = system_ctx("enqueue-due-contributions")
        self.assertTrue(ctx.is_system)
        self.assertEqual(ctx.actor_id, "system:enqueue-due-contributions")
        self.assertIsNone(ctx.actor_role)

    def test_correlation_namespaced_per_job(self):
        ctx = system_ctx("reconcile-stuck-payments")
        self.assertTrue(ctx.correlation_id.startswith("sys:reconcile-stuck-payments:"))

    def test_two_runs_get_distinct_correlations(self):
        self.assertNotEqual(
            system_ctx("noop").correlation_id, system_ctx("noop").correlation_id
        )

    def test_explicit_correlation_override(self):
        self.assertEqual(system_ctx("noop", correlation_id="c9").correlation_id, "c9")


@tag("unit")
class CommandContextMarkerTests(SimpleTestCase):
    databases: list[str] = []

    def test_default_is_not_system(self):
        self.assertFalse(_origin_ctx().is_system)

    def test_build_never_marks_system(self):
        # build() preserves its signature and never sets the SYSTEM marker — only
        # system_ctx does, so the marker is un-forgeable from request data.
        self.assertFalse(
            CommandContext.build(
                actor_id="u", actor_role="r", actor_name="n",
                method="POST", path="/p", body={"a": 1},
            ).is_system
        )


@tag("unit")
class EnqueueInlineTests(SimpleTestCase):
    databases: list[str] = []

    def _register_spy(self, name: str):
        calls: list = []
        register_task(name, lambda payload, ctx: calls.append((payload, ctx)))
        self.addCleanup(enqueue.TASK_HANDLERS.pop, name, None)
        return calls

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_inline_runs_same_callable_with_fresh_system_ctx(self):
        calls = self._register_spy("test-spy")
        origin = _origin_ctx()
        result = enqueue.enqueue("test-spy", {"x": 1}, ctx=origin)

        self.assertIsNone(result)  # enqueue() -> None by contract
        self.assertEqual(len(calls), 1)
        payload, handler_ctx = calls[0]
        self.assertEqual(payload, {"x": 1})
        # The handler runs as SYSTEM, NOT as the enqueuing user.
        self.assertTrue(handler_ctx.is_system)
        self.assertEqual(handler_ctx.actor_id, "system:test-spy")
        self.assertNotEqual(handler_ctx.correlation_id, origin.correlation_id)

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_inline_exception_propagates_to_caller(self):
        def _boom(payload, ctx):
            raise RuntimeError("kaboom")

        register_task("test-boom", _boom)
        self.addCleanup(enqueue.TASK_HANDLERS.pop, "test-boom", None)
        with self.assertRaises(RuntimeError):
            enqueue.enqueue("test-boom", {}, ctx=_origin_ctx())

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_inline_unknown_task_raises(self):
        with self.assertRaises(ValueError):
            enqueue.enqueue("no-such-task", {}, ctx=_origin_ctx())

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_noop_job_enqueues_noop_task(self):
        # The registered noop job enqueues the noop task inline — proving the full
        # scheduler → task → callable loop with no external dependency.
        result = enqueue.SCHEDULER_JOBS["noop"]({}, system_ctx("noop"))
        self.assertEqual(result, {"job": "noop", "enqueued": 1})


@tag("unit")
class EnqueueCloudTests(SimpleTestCase):
    databases: list[str] = []

    @override_settings(TASK_EXECUTION_MODE="cloud", TASKS_AUDIENCE="", TASKS_INVOKER_SA="")
    def test_cloud_mode_unconfigured_or_absent_raises_runtime_error(self):
        # Offline: google-cloud-tasks is absent ⇒ RuntimeError; if present, the
        # empty audience/invoker also raises RuntimeError. Either way it fails loud.
        with self.assertRaises(RuntimeError):
            enqueue.enqueue("process-contribution", {"id": "c1"}, ctx=_origin_ctx())

    @override_settings(TASK_EXECUTION_MODE="cloud")
    def test_cloud_mode_unknown_task_raises_value_error(self):
        with self.assertRaises(ValueError):
            enqueue.enqueue("no-queue-task", {}, ctx=_origin_ctx())


@tag("unit")
class QueueConfigTests(SimpleTestCase):
    databases: list[str] = []

    def test_pinned_queue_max_attempts(self):
        # specs/21 §21.2 pinned values.
        self.assertEqual(QUEUE_CONFIG["process-contribution"]["max_attempts"], 5)
        self.assertEqual(QUEUE_CONFIG["reconcile-contribution"]["max_attempts"], 3)

    def test_unknown_task_falls_back_to_default(self):
        self.assertEqual(enqueue.max_attempts_for("mystery"), DEFAULT_MAX_ATTEMPTS)


@tag("unit")
class IsFinalAttemptTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self):
        self.rf = RequestFactory()

    def _req(self, retry_count=None):
        headers = {}
        if retry_count is not None:
            headers["HTTP_X_CLOUDTASKS_TASKRETRYCOUNT"] = str(retry_count)
        return self.rf.post("/internal/tasks/reconcile-contribution", **headers)

    def test_absent_header_is_not_final_inline(self):
        self.assertFalse(dead_letter.is_final_attempt(self._req(), "reconcile-contribution"))

    def test_final_attempt_at_max_minus_one(self):
        # reconcile-contribution max_attempts=3 → retryCount 2 is the final delivery.
        self.assertTrue(dead_letter.is_final_attempt(self._req(2), "reconcile-contribution"))

    def test_not_final_before_max(self):
        self.assertFalse(dead_letter.is_final_attempt(self._req(1), "reconcile-contribution"))

    def test_beyond_max_is_final(self):
        self.assertTrue(dead_letter.is_final_attempt(self._req(9), "reconcile-contribution"))

    def test_garbage_header_is_not_final(self):
        req = self.rf.post("/internal/tasks/x", HTTP_X_CLOUDTASKS_TASKRETRYCOUNT="nope")
        self.assertFalse(dead_letter.is_final_attempt(req, "reconcile-contribution"))


@tag("unit")
class TaskResponseTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self):
        self.rf = RequestFactory()

    def test_retryable_not_final_returns_503(self):
        req = self.rf.post("/internal/tasks/reconcile-contribution",
                           HTTP_X_CLOUDTASKS_TASKRETRYCOUNT="0")
        resp = dead_letter.task_response(
            retryable=True, ctx=system_ctx("reconcile-contribution"),
            task="reconcile-contribution", request=req, error="transient",
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(json.loads(resp.content)["status"], "RETRY")

    def test_retryable_final_is_dead_lettered_2xx(self):
        req = self.rf.post("/internal/tasks/reconcile-contribution",
                           HTTP_X_CLOUDTASKS_TASKRETRYCOUNT="2")
        resp = dead_letter.task_response(
            retryable=True, ctx=system_ctx("reconcile-contribution"),
            task="reconcile-contribution", request=req, error="gave up",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "DEAD_LETTERED")

    def test_terminal_returns_2xx_dead_lettered(self):
        req = self.rf.post("/internal/tasks/noop")
        resp = dead_letter.task_response(
            retryable=False, ctx=system_ctx("noop"), task="noop",
            request=req, error="bad input",
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "DEAD_LETTERED")


@tag("unit")
class InternalViewTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self):
        self.rf = RequestFactory()

    def _post(self, path, body=None):
        req = self.rf.post(
            path, data=json.dumps(body or {}), content_type="application/json",
        )
        # Simulate InternalOIDCMiddleware having authorized the ingress — the base
        # handler mints SYSTEM only when this marker is present.
        req.internal_verified = True
        return req

    def test_task_handler_runs_callable_and_echoes(self):
        seen: list = []
        register_task("view-spy", lambda payload, ctx: {"echo": payload, "sys": ctx.is_system})
        self.addCleanup(enqueue.TASK_HANDLERS.pop, "view-spy", None)

        resp = views.task_handler(self._post("/internal/tasks/view-spy", {"a": 2}), "view-spy")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(body["status"], "OK")
        self.assertEqual(body["result"], {"echo": {"a": 2}, "sys": True})

    def test_unknown_task_returns_404(self):
        resp = views.task_handler(self._post("/internal/tasks/ghost"), "ghost")
        self.assertEqual(resp.status_code, 404)

    def test_malformed_payload_is_terminal_2xx(self):
        req = self.rf.post("/internal/tasks/noop", data="not json",
                           content_type="application/json")
        req.internal_verified = True
        resp = views.task_handler(req, "noop")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["status"], "DEAD_LETTERED")

    def test_handler_exception_is_retryable_5xx_when_not_final(self):
        register_task("view-boom", lambda payload, ctx: (_ for _ in ()).throw(RuntimeError("x")))
        self.addCleanup(enqueue.TASK_HANDLERS.pop, "view-boom", None)
        resp = views.task_handler(self._post("/internal/tasks/view-boom"), "view-boom")
        self.assertEqual(resp.status_code, 503)

    @override_settings(TASK_EXECUTION_MODE="inline")
    def test_job_handler_runs_noop_job(self):
        resp = views.job_handler(self._post("/internal/jobs/noop"), "noop")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json.loads(resp.content)["result"], {"job": "noop", "enqueued": 1})

    def test_unverified_request_is_403(self):
        # A request that did NOT pass InternalOIDCMiddleware has no
        # internal_verified marker → the base handler mints a NON-SYSTEM ctx and
        # the authority guard fails closed (403) before the (known) task runs.
        req = self.rf.post(
            "/internal/tasks/noop", data="{}", content_type="application/json",
        )  # deliberately NOT setting req.internal_verified
        resp = views.task_handler(req, "noop")
        self.assertEqual(resp.status_code, 403)
