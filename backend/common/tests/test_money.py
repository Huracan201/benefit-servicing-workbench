import unittest

from common.money import cap_posted, dollars, solve_schedule


class SolveScheduleTest(unittest.TestCase):
    def test_corrected_v1_case_3000000_over_36(self):
        schedule = solve_schedule(3_000_000, 36)
        self.assertEqual(len(schedule), 36)
        # Installments 1..35 are the base amount.
        self.assertTrue(all(amount == 83_333 for amount in schedule[:35]))
        # Final installment carries the residual.
        self.assertEqual(schedule[-1], 83_345)
        # Invariant I5: sum is EXACTLY the commitment.
        self.assertEqual(sum(schedule), 3_000_000)

    def test_non_divisible_case(self):
        schedule = solve_schedule(1000, 3)
        # base = 333, remainder = 1 -> [333, 333, 334]
        self.assertEqual(schedule, [333, 333, 334])
        self.assertEqual(sum(schedule), 1000)

    def test_evenly_divisible(self):
        schedule = solve_schedule(1200, 12)
        self.assertEqual(schedule, [100] * 12)
        self.assertEqual(sum(schedule), 1200)

    def test_single_term(self):
        self.assertEqual(solve_schedule(777, 1), [777])

    def test_sums_exactly_across_many_cases(self):
        for total in (0, 1, 99, 100, 101, 999_999, 3_000_001):
            for term in (1, 2, 7, 12, 36, 60):
                sched = solve_schedule(total, term)
                self.assertEqual(len(sched), term)
                self.assertEqual(sum(sched), total, (total, term))
                # §7.3 shape: installments 1..n-1 == base, last carries residual, all >= 0.
                base = total // term
                self.assertTrue(all(a == base for a in sched[:-1]), (total, term))
                self.assertEqual(sched[-1], total - base * (term - 1), (total, term))
                self.assertTrue(all(a >= 0 for a in sched), (total, term))

    def test_invalid_inputs(self):
        with self.assertRaises(ValueError):
            solve_schedule(1000, 0)
        with self.assertRaises(ValueError):
            solve_schedule(-1, 3)
        with self.assertRaises(TypeError):
            solve_schedule(1.5, 3)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            solve_schedule(True, 3)  # bool is not an int here


class CapPostedTest(unittest.TestCase):
    def test_scheduled_is_min(self):
        self.assertEqual(cap_posted(100, 500, 500), 100)

    def test_balance_is_min(self):
        self.assertEqual(cap_posted(100, 40, 500), 40)

    def test_remaining_is_min(self):
        self.assertEqual(cap_posted(100, 500, 25), 25)

    def test_all_equal(self):
        self.assertEqual(cap_posted(50, 50, 50), 50)

    def test_zero_balance(self):
        self.assertEqual(cap_posted(100, 0, 500), 0)

    def test_negative_rejected(self):
        with self.assertRaises(ValueError):
            cap_posted(-1, 100, 100)
        with self.assertRaises(ValueError):
            cap_posted(100, -1, 100)
        with self.assertRaises(ValueError):
            cap_posted(100, 100, -1)

    def test_bool_rejected(self):
        with self.assertRaises(TypeError):
            cap_posted(True, 100, 100)  # bool is not an int here


class DollarsTest(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(dollars(83_345), "$833.45")

    def test_cents_padding(self):
        self.assertEqual(dollars(5), "$0.05")

    def test_thousands_separator(self):
        self.assertEqual(dollars(3_000_000), "$30,000.00")

    def test_negative(self):
        self.assertEqual(dollars(-100), "-$1.00")


if __name__ == "__main__":
    unittest.main()
