"""Pure command-layer unit tests (``@tag('unit')``) — no Firestore, no emulator.

These are the fast, offline half of the CI matrix: the ``python manage.py test
--tag=unit`` gate exists to run exactly these. Before this file there were ZERO
``@tag('unit')`` tests, so that gate ran nothing (specs/08 §8.2 / specs/17). They
pin the normative behaviours that are pure functions of code (no I/O):

* ``commands.base.request_hash`` — determinism, path inclusion (the specs/08
  §8.2 empty-body fix), and canonical-JSON stability under key reordering.
* the ``CommandError`` hierarchy -> HTTP status mapping (specs/11 §11.3).
* the exception-type -> default-severity map + severity ranks (specs/04 §4.10).

They live under the ``payments`` app (which IS in INSTALLED_APPS) so Django's
test runner discovers them; the command layer they exercise is app-independent.
``SimpleTestCase`` + ``databases = []`` keeps them database-free and fast.
"""

from __future__ import annotations

from django.test import SimpleTestCase, tag

from commands.base import (
    BenefitNotAcceptingPayments,
    IdempotencyKeyReused,
    InvalidTransition,
    InvariantViolation,
    NotFound,
    OperationInProgress,
    StaleWrite,
    Unprocessable,
    request_hash,
)
from common.enums import ExceptionType, Severity, severity_rank
from exceptions.service import TYPE_DEFAULT_SEVERITY


@tag("unit")
class RequestHashTests(SimpleTestCase):
    databases: list[str] = []

    def test_deterministic_for_same_method_path_body(self):
        body = {"amountCents": 100, "note": "hi"}
        self.assertEqual(
            request_hash("POST", "/contributions/c1/process", body),
            request_hash("POST", "/contributions/c1/process", body),
        )

    def test_path_included_distinguishes_empty_body_commands(self):
        # specs/08 §8.2: empty-body commands (process) must not let one key replay
        # against a *different* entity — the request path is part of the hash.
        h1 = request_hash("POST", "/contributions/c1/process", None)
        h2 = request_hash("POST", "/contributions/c2/process", None)
        self.assertNotEqual(h1, h2)

    def test_stable_under_dict_key_reordering(self):
        # Canonical JSON sorts keys, so logically-equal bodies hash identically.
        self.assertEqual(
            request_hash("POST", "/x", {"a": 1, "b": 2}),
            request_hash("POST", "/x", {"b": 2, "a": 1}),
        )

    def test_returns_prefixed_sha256(self):
        self.assertTrue(request_hash("POST", "/x", None).startswith("sha256:"))


@tag("unit")
class CommandErrorStatusTests(SimpleTestCase):
    databases: list[str] = []

    def test_http_status_mapping(self):
        # specs/11 §11.3 error taxonomy -> HTTP status.
        cases = [
            (InvalidTransition, 409),
            (InvariantViolation, 409),
            (IdempotencyKeyReused, 409),
            (StaleWrite, 409),
            (BenefitNotAcceptingPayments, 409),
            (Unprocessable, 422),
            (OperationInProgress, 202),
            (NotFound, 404),
        ]
        for cls, expected in cases:
            with self.subTest(error=cls.__name__):
                self.assertEqual(cls.http_status, expected)


@tag("unit")
class ExceptionSeverityTests(SimpleTestCase):
    databases: list[str] = []

    def test_type_default_severity_and_rank(self):
        # specs/04 §4.10 closed map + rank ints (LOW=10 MEDIUM=20 HIGH=30
        # CRITICAL=40).
        cases = [
            (ExceptionType.PAYMENT_FAILED, Severity.HIGH, 30),
            (ExceptionType.PAYMENT_STUCK_PROCESSING, Severity.CRITICAL, 40),
            (ExceptionType.LOAN_BALANCE_MISMATCH, Severity.HIGH, 30),
            (ExceptionType.TASK_FAILED, Severity.HIGH, 30),
            (ExceptionType.SERVICER_SYNC_FAILURE, Severity.MEDIUM, 20),
            (ExceptionType.EMPLOYMENT_VERIFICATION_REQUIRED, Severity.MEDIUM, 20),
            (ExceptionType.BENEFIT_CONFIGURATION_ERROR, Severity.MEDIUM, 20),
        ]
        for exc_type, expected_sev, expected_rank in cases:
            with self.subTest(exception_type=exc_type):
                self.assertEqual(TYPE_DEFAULT_SEVERITY[exc_type], expected_sev)
                self.assertEqual(severity_rank(expected_sev), expected_rank)

    def test_map_covers_every_exception_type(self):
        # The map is closed: every ExceptionType member has a default severity.
        self.assertEqual(set(TYPE_DEFAULT_SEVERITY), set(ExceptionType))
