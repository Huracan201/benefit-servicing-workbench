"""Unit tests for the projection Key layer + :func:`projections.recompute.apply_key`
dispatch — pure, no Firestore (``@tag('unit')``).

Validate the JSON-serializable Key shape, the kind constants, and that
``apply_key`` routes each kind to its recompute function and its gateway writer
(and skips the write when the source entity is gone). Firestore is stubbed out via
``unittest.mock`` so these run without the emulator.
"""

from __future__ import annotations

import unittest
from unittest import mock

from django.test import SimpleTestCase, tag

from projections import recompute


@tag("unit")
class ProjectionKeyShapeTests(SimpleTestCase):
    def test_key_constructors_have_the_canonical_shape(self):
        self.assertEqual(
            recompute.portfolio_current_key(),
            {"kind": "portfolio_current", "id": None, "period": None},
        )
        self.assertEqual(
            recompute.portfolio_period_key("2026-07"),
            {"kind": "portfolio_period", "id": None, "period": "2026-07"},
        )
        self.assertEqual(
            recompute.employer_key("emp_x"),
            {"kind": "employer", "id": "emp_x", "period": None},
        )
        self.assertEqual(
            recompute.employer_period_key("emp_x", "2026-07"),
            {"kind": "employer_period", "id": "emp_x", "period": "2026-07"},
        )
        self.assertEqual(
            recompute.loan_workbench_key("loan_x"),
            {"kind": "loan_workbench", "id": "loan_x", "period": None},
        )

    def test_every_constructor_kind_is_a_known_kind(self):
        for key in (
            recompute.portfolio_current_key(),
            recompute.portfolio_period_key("2026-07"),
            recompute.employer_key("e"),
            recompute.employer_period_key("e", "2026-07"),
            recompute.loan_workbench_key("l"),
        ):
            self.assertIn(key["kind"], recompute.KEY_KINDS)


@tag("unit")
class ApplyKeyDispatchTests(SimpleTestCase):
    def test_unknown_kind_raises(self):
        with self.assertRaises(ValueError):
            recompute.apply_key(object(), {"kind": "nope", "id": None, "period": None})

    def test_portfolio_current_routes_to_current_writer(self):
        client = object()
        derived = {"activeLoans": 3}
        with mock.patch.object(
            recompute, "recompute_portfolio_current", return_value=derived
        ) as rc, mock.patch.object(
            recompute.portfolio_summaries_repo, "write_current"
        ) as write:
            out = recompute.apply_key(client, recompute.portfolio_current_key())
        rc.assert_called_once_with(client)
        write.assert_called_once_with(client, derived)
        self.assertIs(out, derived)

    def test_portfolio_period_routes_with_period(self):
        client = object()
        derived = {"periodLabel": "2026-07"}
        with mock.patch.object(
            recompute, "recompute_portfolio_period", return_value=derived
        ) as rc, mock.patch.object(
            recompute.portfolio_summaries_repo, "write_period"
        ) as write:
            recompute.apply_key(client, recompute.portfolio_period_key("2026-07"))
        rc.assert_called_once_with(client, "2026-07")
        write.assert_called_once_with(client, "2026-07", derived)

    def test_employer_routes_and_skips_write_when_gone(self):
        client = object()
        with mock.patch.object(
            recompute, "recompute_employer", return_value=None
        ) as rc, mock.patch.object(
            recompute.employer_summaries_repo, "write"
        ) as write:
            out = recompute.apply_key(client, recompute.employer_key("emp_x"))
        rc.assert_called_once_with(client, "emp_x")
        write.assert_not_called()
        self.assertIsNone(out)

    def test_employer_routes_and_writes_when_present(self):
        client = object()
        derived = {"employerId": "emp_x"}
        with mock.patch.object(
            recompute, "recompute_employer", return_value=derived
        ), mock.patch.object(
            recompute.employer_summaries_repo, "write"
        ) as write:
            recompute.apply_key(client, recompute.employer_key("emp_x"))
        write.assert_called_once_with(client, "emp_x", derived)

    def test_employer_period_routes_with_id_and_period(self):
        client = object()
        derived = {"periodLabel": "2026-07"}
        with mock.patch.object(
            recompute, "recompute_employer_period", return_value=derived
        ) as rc, mock.patch.object(
            recompute.employer_summaries_repo, "write_period"
        ) as write:
            recompute.apply_key(
                client, recompute.employer_period_key("emp_x", "2026-07")
            )
        rc.assert_called_once_with(client, "emp_x", "2026-07")
        write.assert_called_once_with(client, "emp_x", "2026-07", derived)

    def test_loan_workbench_routes_and_skips_write_when_gone(self):
        client = object()
        with mock.patch.object(
            recompute, "recompute_loan_workbench", return_value=None
        ) as rc, mock.patch.object(
            recompute.loan_workbenches_repo, "write"
        ) as write:
            out = recompute.apply_key(client, recompute.loan_workbench_key("loan_x"))
        rc.assert_called_once_with(client, "loan_x")
        write.assert_not_called()
        self.assertIsNone(out)


if __name__ == "__main__":
    unittest.main()
