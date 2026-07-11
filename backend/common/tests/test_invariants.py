import unittest

from common.errors import InvariantViolation
from common.invariants import (
    check_amount_paid_within_commitment,
    check_loan_balance_non_negative,
    check_mutual_pointers,
    check_posted_immutable,
    check_posted_within_caps,
    check_remaining_commitment_consistent,
    check_schedule_sums_to_commitment,
)


class InvariantPassTest(unittest.TestCase):
    def test_i1_ok(self):
        check_loan_balance_non_negative(0)
        check_loan_balance_non_negative(500)

    def test_i2_ok(self):
        check_amount_paid_within_commitment(500, 500)
        check_amount_paid_within_commitment(0, 500)

    def test_i3_ok(self):
        check_remaining_commitment_consistent(2_166_670, 3_000_000, 833_330)

    def test_i4_ok(self):
        check_posted_within_caps(40, 100, 40, 500)  # capped by balance
        check_posted_within_caps(100, 100, 500, 500)

    def test_i5_ok(self):
        check_schedule_sums_to_commitment([83_333] * 35 + [83_345], 3_000_000)

    def test_i6_ok_when_unchanged(self):
        check_posted_immutable("POSTED", 100, 100)
        check_posted_immutable("PROCESSING", 100, 250)  # not posted yet -> free

    def test_i7_ok(self):
        check_mutual_pointers("ben_1", "loan_1", "loan_1", "ben_1")


class InvariantRaiseTest(unittest.TestCase):
    def test_i1_negative_balance(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_loan_balance_non_negative(-1)
        self.assertEqual(ctx.exception.invariant, "I1")

    def test_i2_overpay(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_amount_paid_within_commitment(501, 500)
        self.assertEqual(ctx.exception.invariant, "I2")

    def test_i3_inconsistent(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_remaining_commitment_consistent(999, 3_000_000, 833_330)
        self.assertEqual(ctx.exception.invariant, "I3")

    def test_i4_exceeds_cap(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_posted_within_caps(101, 100, 500, 500)
        self.assertEqual(ctx.exception.invariant, "I4")

    def test_i4_negative_posted(self):
        with self.assertRaises(InvariantViolation):
            check_posted_within_caps(-1, 100, 500, 500)

    def test_i5_sum_mismatch(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_schedule_sums_to_commitment([83_333] * 36, 3_000_000)
        self.assertEqual(ctx.exception.invariant, "I5")

    def test_i6_posted_mutated(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_posted_immutable("POSTED", 100, 250)
        self.assertEqual(ctx.exception.invariant, "I6")

    def test_i7_broken_pointer(self):
        with self.assertRaises(InvariantViolation) as ctx:
            check_mutual_pointers("ben_X", "loan_1", "loan_1", "ben_1")
        self.assertEqual(ctx.exception.invariant, "I7")
        with self.assertRaises(InvariantViolation):
            check_mutual_pointers("ben_1", "loan_X", "loan_1", "ben_1")


if __name__ == "__main__":
    unittest.main()
