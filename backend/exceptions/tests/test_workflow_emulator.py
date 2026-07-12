"""Emulator integration tests for the exception WORKFLOW commands (specs/11 §11.4).

Drives the operator-facing lifecycle of a manual, loan-scoped operational
exception directly against Firestore (no HTTP):

    create (openExceptionCount +1, OPEN)
      -> assign (status-neutral: still OPEN)
      -> mark-in-review (IN_REVIEW)
      -> resolve (RESOLVED + openExceptionCount -1 + EXCEPTION_RESOLVED event).
"""

from __future__ import annotations

import os
import unittest

from django.test import SimpleTestCase, tag

from benefits.tests.domain_graph import count_events, make_ctx, seed_active_graph, unique_key
from common.enums import ExceptionStatus, ExceptionType
from common.firestore import get_client
from exceptions.commands import (
    assign_exception,
    create_exception,
    mark_in_review,
    resolve_exception,
)


EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST"))


@tag("emulator")
@unittest.skipUnless(EMULATOR, "requires FIRESTORE_EMULATOR_HOST")
class ExceptionWorkflowTests(SimpleTestCase):
    databases: list[str] = []

    def test_create_assign_review_resolve(self):
        client = get_client()
        key = unique_key("exc")
        g = seed_active_graph(client, key, term_months=1)
        self.assertEqual(g.loan()["openExceptionCount"], 0)

        # --- create (loan-scoped) -> OPEN, openExceptionCount +1 ----------
        created = create_exception(
            ctx=make_ctx(),
            exception_type=str(ExceptionType.EMPLOYMENT_VERIFICATION_REQUIRED),
            entity_type="LOAN",
            entity_id=g.loan_id,
            summary="Manual verification needed",
            client=client,
        )
        exc_id = created["exceptionId"]
        self.assertEqual(created["status"], str(ExceptionStatus.OPEN))
        self.assertEqual(g.loan()["openExceptionCount"], 1)
        self.assertEqual(g.exception(exc_id)["status"], str(ExceptionStatus.OPEN))
        self.assertEqual(
            count_events(client, event_type="EXCEPTION_CREATED", loan_id=g.loan_id), 1
        )

        # --- assign (STATUS-NEUTRAL: still OPEN) --------------------------
        assigned = assign_exception(
            exc_id, ctx=make_ctx(), assign_to="user_reviewer", client=client
        )
        self.assertEqual(assigned["assignedTo"], "user_reviewer")
        self.assertEqual(assigned["status"], str(ExceptionStatus.OPEN))
        self.assertEqual(g.exception(exc_id)["status"], str(ExceptionStatus.OPEN))
        self.assertEqual(g.exception(exc_id)["assignedTo"], "user_reviewer")
        # count unchanged by an assign
        self.assertEqual(g.loan()["openExceptionCount"], 1)

        # --- mark-in-review -> IN_REVIEW ----------------------------------
        reviewed = mark_in_review(exc_id, ctx=make_ctx(), client=client)
        self.assertEqual(reviewed["status"], str(ExceptionStatus.IN_REVIEW))
        self.assertEqual(g.exception(exc_id)["status"], str(ExceptionStatus.IN_REVIEW))

        # --- resolve -> RESOLVED + count -1 + event -----------------------
        resolved = resolve_exception(
            exc_id, ctx=make_ctx(), note="verified manually", client=client
        )
        self.assertEqual(resolved["status"], str(ExceptionStatus.RESOLVED))
        exc = g.exception(exc_id)
        self.assertEqual(exc["status"], str(ExceptionStatus.RESOLVED))
        self.assertEqual(exc["resolution"]["resolvedBy"], "user_test_manager")
        self.assertEqual(exc["resolution"]["note"], "verified manually")
        self.assertIsNotNone(exc["resolution"]["resolvedByEvent"])
        self.assertEqual(g.loan()["openExceptionCount"], 0)
        self.assertEqual(
            count_events(client, event_type="EXCEPTION_RESOLVED", loan_id=g.loan_id), 1
        )
