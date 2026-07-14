"""Pure unit test (``@tag('unit')``) that the command surface emits a structured
completion log (specs/16 §16.2) — the substrate is wired live, and the idempotency
key is hashed (never logged raw).
"""

from __future__ import annotations

from django.test import SimpleTestCase, tag

from benefits.views import _respond
from commands.base import CommandContext, StaleWrite


def _ctx() -> CommandContext:
    return CommandContext(
        actor_id="u1",
        actor_role="SERVICING_MANAGER",
        actor_name="Op",
        idempotency_key="raw-secret-idem-key",
    )


@tag("unit")
class CommandCompletionLogTests(SimpleTestCase):
    databases: list[str] = []

    def test_success_emits_structured_line_with_hashed_key(self) -> None:
        def ok_command(agreement_id, ctx):  # matches _respond's call shape
            return {"status": "SUSPENDED"}

        with self.assertLogs("bsw.command", level="INFO") as cm:
            resp = _respond(ok_command, "agr-1", _ctx())

        self.assertEqual(resp.status_code, 200)
        rec = cm.records[-1]
        self.assertEqual(rec.operation, "ok_command")
        self.assertEqual(rec.entityId, "agr-1")
        self.assertEqual(rec.result, "OK")
        self.assertIsInstance(rec.durationMs, int)
        # The raw idempotency key must NEVER appear in a log — log_event hashes it.
        self.assertNotEqual(getattr(rec, "idempotencyKey", ""), "raw-secret-idem-key")
        self.assertTrue(getattr(rec, "idempotencyKey", ""))

    def test_command_error_logs_the_error_code(self) -> None:
        def failing_command(agreement_id, ctx):
            raise StaleWrite("changed under you")

        with self.assertLogs("bsw.command", level="WARNING") as cm:
            resp = _respond(failing_command, "agr-2", _ctx())

        self.assertEqual(resp.status_code, 409)
        rec = cm.records[-1]
        self.assertEqual(rec.result, "ERROR")
        self.assertEqual(rec.errorCode, "STALE_WRITE")
