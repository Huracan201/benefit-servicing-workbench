"""Pure unit tests (``@tag('unit')``) for the command-boundary authority guard.

Offline, database-free (``SimpleTestCase`` + ``databases = []``): they pin the
defense-in-depth contract of :func:`commands.authz.require_system_or_role`
(specs/12 §12.5) without an emulator —

* a SYSTEM ctx (``actor_role=None``, rank ``-1``) is PERMITTED — the special-case
  that must run before the role check, else every legitimate async task is
  false-rejected;
* a user ctx below ``min_role`` is REJECTED with a 403-mapped ``CommandError``,
  even though the ingress middleware would normally have stopped it earlier —
  proving the guard is a second, independent check;
* a user ctx at/above ``min_role`` is PERMITTED;
* the ``/internal`` base view mints SYSTEM only for a request that passed ingress
  (``request.internal_verified``); an unverified request is minted NON-SYSTEM and
  denied with a hard 403 at the command boundary, never running the handler.
"""

from __future__ import annotations

import json

from django.test import RequestFactory, SimpleTestCase, tag

from commands.authz import AuthorityDenied, require_system_or_role
from commands.base import CommandContext, CommandError
from firebase_auth.permissions import (
    ADMINISTRATOR,
    OPERATIONS_USER,
    SERVICING_MANAGER,
)
from internal import views
from internal.enqueue import register_task
from internal import enqueue
from internal.system_context import system_ctx


def _user_ctx(role) -> CommandContext:
    """A request-derived (non-SYSTEM) context carrying ``role``."""
    return CommandContext(actor_id="user-1", actor_role=role, actor_name="Op")


@tag("unit")
class RequireSystemOrRoleTests(SimpleTestCase):
    databases: list[str] = []

    def test_system_ctx_is_permitted_despite_none_role(self):
        # SYSTEM has actor_role=None (rank -1); the marker must be honored FIRST,
        # otherwise the role comparison would reject every legitimate async task.
        ctx = system_ctx("reconcile-stuck-payments")
        self.assertIsNone(ctx.actor_role)
        self.assertIsNone(require_system_or_role(ctx, min_role=ADMINISTRATOR))

    def test_user_below_min_role_is_rejected(self):
        # Defense-in-depth: even if ingress were bypassed, the command boundary
        # rejects an under-privileged non-SYSTEM caller.
        with self.assertRaises(AuthorityDenied):
            require_system_or_role(_user_ctx(OPERATIONS_USER), min_role=ADMINISTRATOR)

    def test_rejection_is_403_command_error(self):
        try:
            require_system_or_role(_user_ctx(SERVICING_MANAGER), min_role=ADMINISTRATOR)
        except CommandError as exc:
            self.assertEqual(exc.http_status, 403)
            self.assertEqual(exc.code, "FORBIDDEN")
        else:
            self.fail("expected AuthorityDenied")

    def test_user_at_min_role_is_permitted(self):
        self.assertIsNone(
            require_system_or_role(_user_ctx(ADMINISTRATOR), min_role=ADMINISTRATOR)
        )

    def test_user_above_min_role_is_permitted(self):
        self.assertIsNone(
            require_system_or_role(_user_ctx(SERVICING_MANAGER), min_role=OPERATIONS_USER)
        )

    def test_unknown_role_is_rejected(self):
        with self.assertRaises(AuthorityDenied):
            require_system_or_role(_user_ctx("MYSTERY"), min_role=OPERATIONS_USER)

    def test_unknown_min_role_fails_closed(self):
        # A missing/unknown min_role is a programming error: the guard must fail
        # closed, not admit everyone (role_rank('BOGUS') == -1 would otherwise be
        # satisfied by every actor, including a role-less one).
        with self.assertRaises(AuthorityDenied):
            require_system_or_role(_user_ctx(ADMINISTRATOR), min_role="BOGUS")


@tag("unit")
class InternalViewAuthorityTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self):
        self.rf = RequestFactory()

    def test_verified_system_task_is_not_blocked_by_guard(self):
        # The guard is transparent to real async work: a request that passed
        # ingress (internal_verified) is minted SYSTEM by the view and runs.
        register_task("authz-spy", lambda payload, ctx: {"ran": ctx.is_system})
        self.addCleanup(enqueue.TASK_HANDLERS.pop, "authz-spy", None)
        req = self.rf.post("/internal/tasks/authz-spy", data="{}",
                           content_type="application/json")
        req.internal_verified = True
        resp = views.task_handler(req, "authz-spy")
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.content)
        self.assertEqual(body["status"], "OK")
        self.assertEqual(body["result"], {"ran": True})

    def test_unverified_request_is_denied_403(self):
        # The crux of the guard's value: a request that did NOT pass ingress (no
        # internal_verified marker — a bypassed/misconfigured middleware) is
        # minted NON-SYSTEM and denied with a hard 403 at the command boundary,
        # never running the registered callable.
        ran: list = []
        register_task("authz-guarded", lambda payload, ctx: ran.append(True) or {"ran": True})
        self.addCleanup(enqueue.TASK_HANDLERS.pop, "authz-guarded", None)
        req = self.rf.post("/internal/tasks/authz-guarded", data="{}",
                           content_type="application/json")  # no internal_verified
        resp = views.task_handler(req, "authz-guarded")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(ran, [])  # the callable never ran
