import unittest
from datetime import date, datetime, timezone

from common.periods import (
    SCHEDULED_TIME,
    SYSTEM_TIMEZONE,
    period_label,
    scheduled_datetime,
    shift_months,
)


class PeriodLabelTest(unittest.TestCase):
    def test_naive_treated_as_system_tz(self):
        self.assertEqual(period_label(datetime(2026, 7, 11, 9, 0)), "2026-07")

    def test_utc_converted_to_system_tz(self):
        # An aware UTC instant near a month boundary must be bucketed by its LOCAL
        # month in SYSTEM_TIMEZONE, not its raw UTC month (unconditional: if
        # period_label failed to convert, `local` below would disagree).
        utc_dt = datetime(2026, 1, 1, 2, 0, tzinfo=timezone.utc)
        local = utc_dt.astimezone(SYSTEM_TIMEZONE)
        self.assertEqual(period_label(utc_dt), f"{local.year:04d}-{local.month:02d}")
        # In the default America/New_York tz (UTC-05 in January) this rolls back to Dec 2025.
        if str(SYSTEM_TIMEZONE) == "America/New_York":
            self.assertEqual(period_label(utc_dt), "2025-12")

    def test_zero_padding(self):
        self.assertEqual(period_label(datetime(2026, 3, 5)), "2026-03")


class ShiftMonthsTest(unittest.TestCase):
    def test_forward(self):
        self.assertEqual(shift_months(date(2026, 1, 15), 1), date(2026, 2, 15))

    def test_year_rollover(self):
        self.assertEqual(shift_months(date(2026, 11, 10), 3), date(2027, 2, 10))

    def test_day_clamped_to_month_end(self):
        # Jan 31 + 1 month -> Feb 28 (2026 not a leap year).
        self.assertEqual(shift_months(date(2026, 1, 31), 1), date(2026, 2, 28))
        # Leap year.
        self.assertEqual(shift_months(date(2028, 1, 31), 1), date(2028, 2, 29))

    def test_negative(self):
        self.assertEqual(shift_months(date(2026, 3, 15), -3), date(2025, 12, 15))

    def test_zero(self):
        self.assertEqual(shift_months(date(2026, 6, 30), 0), date(2026, 6, 30))


class ScheduledDatetimeTest(unittest.TestCase):
    def test_installment_one_is_start_month_at_noon(self):
        dt = scheduled_datetime(date(2026, 7, 15), 1)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 7, 15))
        self.assertEqual(dt.hour, 12)
        self.assertEqual(dt.minute, 0)
        self.assertEqual(dt.tzinfo, SYSTEM_TIMEZONE)

    def test_noon_rule_time(self):
        dt = scheduled_datetime(date(2026, 7, 15), 5)
        self.assertEqual(dt.timetz().replace(tzinfo=None), SCHEDULED_TIME)

    def test_installment_n_shifts_months(self):
        # Installment 4 is 3 months after the start month, same day-of-month.
        dt = scheduled_datetime(date(2026, 7, 15), 4)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 10, 15))

    def test_deterministic(self):
        a = scheduled_datetime(date(2026, 7, 15), 12)
        b = scheduled_datetime(date(2026, 7, 15), 12)
        self.assertEqual(a, b)

    def test_invalid_installment(self):
        with self.assertRaises(ValueError):
            scheduled_datetime(date(2026, 7, 15), 0)


if __name__ == "__main__":
    unittest.main()
