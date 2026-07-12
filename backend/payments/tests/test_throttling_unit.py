"""Pure view-layer unit tests for DRF ``ScopedRateThrottle`` on the mutating
command endpoints (specs/19 Phase-3 security prerequisite; the "no rate limiting
on the mutating money API" finding in engineering-reports/security-review-phase-1-2
§7/§8).

These are ``@tag('unit')`` tests — no Firestore, no emulator. The command services
are mocked, so a request that clears auth + role + the ``Idempotency-Key`` guard
reaches the throttle. Throttling runs in DRF's ``initial()`` **before** ``post()``
(``perform_authentication`` → ``check_permissions`` → ``check_throttles``), so the
throttled request never reaches the command — which is exactly what we assert.

We lower every per-scope rate to a small ``_LIMIT`` (so the ``_LIMIT+1``-th call
trips deterministically) by patching the throttle's bound ``THROTTLE_RATES`` in
``setUp`` — DRF reads it as a class attribute fixed at import, so an
``override_settings(REST_FRAMEWORK=...)`` would NOT take effect. The throttle
class and the specs/11 §11.3 ``EXCEPTION_HANDLER`` stay exactly as production.
``ScopedRateThrottle``'s cache key is ``(scope, request.user.pk)`` and
:attr:`FirebasePrincipal.pk` is the uid, so the counters are per ``(scope, uid)``.
That keying is what makes a *different scope* and a *different uid* independent —
the two isolation tests below. The backing ``LocMemCache`` is process-global, so
each test starts from cleared counters.

Placed under the ``payments`` app (in ``INSTALLED_APPS``) so Django's test runner
discovers it; the config it exercises is app-independent, matching
``payments/tests/test_command_layer_unit.py``. ``SimpleTestCase`` + ``databases =
[]`` keeps it database-free and fast.
"""

from __future__ import annotations

from unittest import mock

from django.conf import settings as django_settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings, tag
from rest_framework.test import APIRequestFactory, force_authenticate

from firebase_auth.authentication import FirebasePrincipal
from notes.views import AddNoteView
from payments.views import ProcessContributionView

# A small ceiling so the (N+1)th call trips the limiter deterministically:
# "2/min" -> 2 allowed, 3rd throttled.
_LIMIT = 2

# DRF binds ``SimpleRateThrottle.THROTTLE_RATES`` (= ``DEFAULT_THROTTLE_RATES``) as
# a CLASS attribute at import time and never re-reads api_settings, so an
# ``@override_settings(REST_FRAMEWORK=...)`` does NOT change the effective rate.
# setUp() therefore patches that bound class attribute directly. The throttle
# class and the specs/11 §11.3 exception handler (which renders a Throttled 429 in
# the {error:{code}} envelope) stay untouched — this exercises the real wiring.

# A dedicated locmem cache isolates the throttle counters from any other cache
# use and from other test modules sharing the process.
_TEST_CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "throttle-unit-test",
    }
}


def _manager(uid: str = "u_mgr") -> FirebasePrincipal:
    """A SERVICING_MANAGER — satisfies both RequireManager (payments-write) and
    RequireOperations (note-write), so one principal drives both scopes."""
    return FirebasePrincipal(
        uid=uid, claims={"role": "SERVICING_MANAGER", "name": "Mgr"}
    )


@tag("unit")
@override_settings(CACHES=_TEST_CACHES)
class ScopedRateThrottleTests(SimpleTestCase):
    databases: list[str] = []

    def setUp(self):
        self.factory = APIRequestFactory()
        # DRF binds SimpleRateThrottle.THROTTLE_RATES as a class attribute at
        # import, so an @override_settings on REST_FRAMEWORK does NOT change the
        # effective rate. Patch the bound class attribute directly so the low
        # per-scope limits actually take effect.
        prod_rates = django_settings.REST_FRAMEWORK.get("DEFAULT_THROTTLE_RATES", {})
        low_rates = {scope: f"{_LIMIT}/min" for scope in prod_rates}
        patcher = mock.patch.dict(
            "rest_framework.throttling.SimpleRateThrottle.THROTTLE_RATES",
            low_rates,
            clear=False,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        # LocMemCache is process-global; every test starts from empty counters.
        cache.clear()
        self.addCleanup(cache.clear)

    # -- request helpers -------------------------------------------------
    def _process(self, uid: str = "u_mgr"):
        """One POST /contributions/c1/process as ``uid`` (scope payments-write)."""
        request = self.factory.post(
            "/api/v1/contributions/c1/process",
            {},
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-thr",
        )
        force_authenticate(request, user=_manager(uid))
        return ProcessContributionView.as_view()(request, contributionId="c1")

    def _add_note(self, uid: str = "u_mgr"):
        """One POST /loans/loan_1/notes as ``uid`` (scope note-write)."""
        request = self.factory.post(
            "/api/v1/loans/loan_1/notes",
            {"text": "n"},
            format="json",
            HTTP_IDEMPOTENCY_KEY="k-note",
        )
        force_authenticate(request, user=_manager(uid))
        return AddNoteView.as_view()(request, loan_id="loan_1")

    # -- the limiter fires ----------------------------------------------
    @mock.patch("payments.views.process_contribution")
    def test_request_beyond_scope_limit_is_429(self, mock_process):
        mock_process.return_value = {"contributionId": "c1", "status": "COMPLETED"}
        # The first _LIMIT requests are allowed (the command runs).
        for _ in range(_LIMIT):
            self.assertEqual(self._process().status_code, 200)
        # The (N+1)th trips the limiter -> 429, and the command is NOT reached:
        # the throttle fires in DRF initial(), before post() runs.
        throttled = self._process()
        self.assertEqual(throttled.status_code, 429)
        self.assertEqual(mock_process.call_count, _LIMIT)

    @mock.patch("payments.views.process_contribution")
    def test_throttled_response_uses_clean_error_envelope(self, mock_process):
        # NOTE: this asserts the specs/11 §11.3 shape, which the shared
        # core.exception_handler renders for a DRF Throttled (see module note /
        # the U0a followups): { "error": { "code", ... } } — never DRF's raw
        # {"detail": ...}. Rendered centrally, so no view needs throttle handling.
        mock_process.return_value = {"contributionId": "c1", "status": "COMPLETED"}
        for _ in range(_LIMIT):
            self._process()
        throttled = self._process()
        self.assertEqual(throttled.status_code, 429)
        self.assertIn("error", throttled.data)
        self.assertTrue(throttled.data["error"].get("code"))

    # -- isolation: the cache key is (scope, uid) -----------------------
    @mock.patch("notes.views.add_note")
    @mock.patch("payments.views.process_contribution")
    def test_a_different_scope_is_unaffected(self, mock_process, mock_add_note):
        mock_process.return_value = {"contributionId": "c1", "status": "COMPLETED"}
        mock_add_note.return_value = {"noteId": "note_1"}
        # Exhaust payments-write for this uid.
        for _ in range(_LIMIT):
            self._process()
        self.assertEqual(self._process().status_code, 429)
        # note-write is a SEPARATE bucket (scope is part of the cache key) and is
        # still open — the same uid can add a note.
        self.assertEqual(self._add_note().status_code, 201)
        mock_add_note.assert_called_once()

    @mock.patch("payments.views.process_contribution")
    def test_a_different_uid_is_unaffected(self, mock_process):
        mock_process.return_value = {"contributionId": "c1", "status": "COMPLETED"}
        # uid A spends its whole payments-write budget, then is throttled.
        for _ in range(_LIMIT):
            self.assertEqual(self._process(uid="u_a").status_code, 200)
        self.assertEqual(self._process(uid="u_a").status_code, 429)
        # uid B has its OWN counter (ident = request.user.pk in the cache key),
        # so it is unaffected by A hitting the limit.
        self.assertEqual(self._process(uid="u_b").status_code, 200)
