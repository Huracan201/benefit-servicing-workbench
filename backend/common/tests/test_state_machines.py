import unittest

from common.errors import InvalidTransition
from common.state_machines import (
    ATTEMPT_TRANSITIONS,
    BENEFIT_TRANSITIONS,
    CONTRIBUTION_TRANSITIONS,
    EMPLOYER_TRANSITIONS,
    EMPLOYMENT_TRANSITIONS,
    EXCEPTION_TRANSITIONS,
    LOAN_TRANSITIONS,
    assert_transition,
    can_transition,
    get_machine,
)

ALL = {
    "contribution": CONTRIBUTION_TRANSITIONS,
    "attempt": ATTEMPT_TRANSITIONS,
    "benefit": BENEFIT_TRANSITIONS,
    "exception": EXCEPTION_TRANSITIONS,
    "employment": EMPLOYMENT_TRANSITIONS,
    "loan": LOAN_TRANSITIONS,
    "employer": EMPLOYER_TRANSITIONS,
}

# Independent, spec-encoded ground truth (specs/06) — hardcoded here so a
# regression that drops/adds a transition in the source is caught (the source
# frozensets are NOT the oracle for this test).
CANONICAL_TABLES = {
    "contribution": {
        ("SCHEDULED", "PROCESSING"), ("SCHEDULED", "CANCELED"),
        ("PROCESSING", "POSTED"), ("PROCESSING", "FAILED"), ("PROCESSING", "CANCELED"),
        ("FAILED", "RETRY_PENDING"), ("FAILED", "CANCELED"),
        ("RETRY_PENDING", "PROCESSING"), ("RETRY_PENDING", "CANCELED"),
    },
    "attempt": {("STARTED", "SUCCEEDED"), ("STARTED", "FAILED")},
    "benefit": {
        ("DRAFT", "PENDING"), ("PENDING", "ACTIVATING"),
        ("ACTIVATING", "ACTIVE"), ("ACTIVATING", "PENDING"), ("ACTIVATING", "TERMINATED"),
        ("ACTIVE", "SUSPENDED"), ("ACTIVE", "TERMINATED"),
        ("SUSPENDED", "ACTIVE"), ("SUSPENDED", "TERMINATED"),
        ("ACTIVE", "COMPLETED"), ("SUSPENDED", "COMPLETED"),
    },
    "exception": {
        ("OPEN", "IN_REVIEW"), ("OPEN", "RESOLVED"), ("OPEN", "DISMISSED"),
        ("IN_REVIEW", "RESOLVED"), ("IN_REVIEW", "DISMISSED"),
    },
    "employment": {
        ("PENDING", "ACTIVE"), ("ACTIVE", "LEAVE"), ("LEAVE", "ACTIVE"),
        ("ACTIVE", "TERMINATED"), ("LEAVE", "TERMINATED"),
    },
    "loan": {
        ("ACTIVE", "PAID_OFF"), ("ACTIVE", "DELINQUENT"), ("DELINQUENT", "ACTIVE"),
        ("ACTIVE", "CLOSED"), ("PAID_OFF", "CLOSED"),
    },
    "employer": {("ACTIVE", "INACTIVE"), ("INACTIVE", "ACTIVE")},
}


class ExactTablesTest(unittest.TestCase):
    """Lock every machine's table against specs/06 — not just contribution."""

    def test_all_tables_exact(self):
        self.assertEqual(set(ALL), set(CANONICAL_TABLES))  # same set of machines
        for name, expected in CANONICAL_TABLES.items():
            self.assertEqual(
                set(ALL[name]), expected, f"{name} table drifted from specs/06"
            )

    def test_no_self_loops(self):
        for name, transitions in ALL.items():
            for frm, to in transitions:
                self.assertNotEqual(frm, to, (name, frm, to))


# Representative disallowed transitions (specs/06). Every one must raise.
DISALLOWED = {
    "contribution": [
        ("POSTED", "PROCESSING"),   # POSTED is terminal/immutable
        ("SCHEDULED", "POSTED"),    # must pass through PROCESSING
        ("CANCELED", "PROCESSING"),
        ("FAILED", "POSTED"),
        ("POSTED", "CANCELED"),
        ("SCHEDULED", "FAILED"),
    ],
    "attempt": [
        ("SUCCEEDED", "FAILED"),
        ("FAILED", "SUCCEEDED"),
        ("STARTED", "STARTED"),
    ],
    "benefit": [
        ("DRAFT", "ACTIVE"),
        ("COMPLETED", "ACTIVE"),
        ("TERMINATED", "ACTIVE"),
        ("PENDING", "ACTIVE"),
        ("ACTIVE", "PENDING"),
    ],
    "exception": [
        ("RESOLVED", "OPEN"),
        ("DISMISSED", "IN_REVIEW"),
        ("OPEN", "OPEN"),
    ],
    "employment": [
        ("PENDING", "LEAVE"),
        ("TERMINATED", "ACTIVE"),
        ("LEAVE", "PENDING"),
    ],
    "loan": [
        ("PAID_OFF", "ACTIVE"),
        ("CLOSED", "ACTIVE"),
        ("DELINQUENT", "PAID_OFF"),
    ],
    "employer": [
        ("ACTIVE", "ACTIVE"),
        ("INACTIVE", "INACTIVE"),
    ],
}


class AllowedTransitionsTest(unittest.TestCase):
    def test_every_allowed_transition_passes(self):
        for name, transitions in ALL.items():
            machine = get_machine(name)
            for frm, to in transitions:
                self.assertTrue(machine.can_transition(frm, to), (name, frm, to))
                self.assertTrue(can_transition(name, frm, to), (name, frm, to))
                # assert_transition must NOT raise for an allowed transition.
                assert_transition(name, frm, to)

    def test_contribution_table_exact(self):
        self.assertEqual(
            CONTRIBUTION_TRANSITIONS,
            frozenset({
                ("SCHEDULED", "PROCESSING"),
                ("SCHEDULED", "CANCELED"),
                ("PROCESSING", "POSTED"),
                ("PROCESSING", "FAILED"),
                ("PROCESSING", "CANCELED"),
                ("FAILED", "RETRY_PENDING"),
                ("FAILED", "CANCELED"),
                ("RETRY_PENDING", "PROCESSING"),
                ("RETRY_PENDING", "CANCELED"),
            }),
        )


class DisallowedTransitionsTest(unittest.TestCase):
    def test_representative_disallowed_raise(self):
        for name, cases in DISALLOWED.items():
            for frm, to in cases:
                self.assertFalse(can_transition(name, frm, to), (name, frm, to))
                with self.assertRaises(InvalidTransition):
                    assert_transition(name, frm, to)

    def test_invalid_transition_carries_context(self):
        try:
            assert_transition("contribution", "POSTED", "PROCESSING")
        except InvalidTransition as exc:
            self.assertEqual(exc.machine, "contribution")
            self.assertEqual(exc.frm, "POSTED")
            self.assertEqual(exc.to, "PROCESSING")
        else:
            self.fail("expected InvalidTransition")

    def test_unknown_machine_raises_keyerror(self):
        with self.assertRaises(KeyError):
            get_machine("nope")


if __name__ == "__main__":
    unittest.main()
