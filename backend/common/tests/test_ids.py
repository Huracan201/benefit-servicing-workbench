import unittest

from common.enums import ExceptionType
from common.ids import attempt_id, contribution_id, exception_id, processor_key


class IdFormatTest(unittest.TestCase):
    def test_contribution_id(self):
        self.assertEqual(contribution_id("ben_jordan_001", 4), "ben_jordan_001__004")

    def test_contribution_id_pads_to_three(self):
        self.assertEqual(contribution_id("a", 1), "a__001")
        self.assertEqual(contribution_id("a", 36), "a__036")

    def test_contribution_id_large(self):
        self.assertEqual(contribution_id("a", 120), "a__120")

    def test_attempt_id(self):
        cid = contribution_id("ben_jordan_001", 4)
        self.assertEqual(attempt_id(cid, 2), "ben_jordan_001__004__att_002")

    def test_exception_id_with_enum(self):
        self.assertEqual(
            exception_id("ben_jordan_001__004", ExceptionType.PAYMENT_FAILED),
            "ben_jordan_001__004__PAYMENT_FAILED",
        )

    def test_exception_id_with_string(self):
        self.assertEqual(
            exception_id("ent1", "TASK_FAILED"),
            "ent1__TASK_FAILED",
        )

    def test_processor_key(self):
        cid = contribution_id("ben_jordan_001", 4)
        self.assertEqual(processor_key(cid, 2), "pay_ben_jordan_001__004_att_002")

    def test_positive_number_required(self):
        for fn in (lambda: contribution_id("a", 0), lambda: attempt_id("a", 0),
                   lambda: processor_key("a", 0)):
            with self.assertRaises(ValueError):
                fn()
        with self.assertRaises(TypeError):
            contribution_id("a", True)


if __name__ == "__main__":
    unittest.main()
