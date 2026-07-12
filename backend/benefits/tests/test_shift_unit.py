"""Pure unit tests (``@tag('unit')``) for the schedule-shift math — no Firestore.

The ``python manage.py test --tag=unit`` gate is the fast, offline half of CI;
these extend it to the Phase-2-part-2 command layer by pinning
:func:`benefits.shift._months_between` — the whole-month, round-a-partial-month-UP
suspension-duration rule (specs/07 §7.8) that both :func:`benefits.shift.shift_schedule`
and ``benefits.services.resume_benefit`` rely on to size a schedule shift.

The round-UP behaviour is exactly what makes the naive "back-date 2 months →
shift is 2" assumption a date-bomb: on day-of-month clamp edges (Jan 31, Apr 30,
Aug 31) ``startDate + 2 months`` lands *before* the resume date, so the real
shift rounds up to 3. These tests lock that in.

``SimpleTestCase`` + ``databases = []`` keeps them database-free and fast; the
function under test is pure stdlib (dates only), so no google.cloud / emulator is
touched.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from django.test import SimpleTestCase, tag

from benefits.shift import _months_between
from common.periods import shift_months


@tag("unit")
class MonthsBetweenTests(SimpleTestCase):
    databases: list[str] = []

    def test_zero_when_from_equals_to(self):
        self.assertEqual(_months_between(date(2026, 1, 15), date(2026, 1, 15)), 0)

    def test_zero_when_from_is_none(self):
        self.assertEqual(_months_between(None, date(2026, 1, 15)), 0)

    def test_zero_when_to_is_none(self):
        self.assertEqual(_months_between(date(2026, 1, 15), None), 0)

    def test_zero_when_resume_before_suspend(self):
        # A resume on/before the suspend instant shifts nothing (specs/07 §7.8).
        self.assertEqual(_months_between(date(2026, 3, 1), date(2026, 1, 1)), 0)

    def test_exact_whole_months(self):
        # Jan 15 -> Mar 15 is exactly two whole months; no partial to round up.
        self.assertEqual(_months_between(date(2026, 1, 15), date(2026, 3, 15)), 2)

    def test_partial_trailing_month_rounds_up(self):
        # Two whole months + a few days -> the partial trailing month rounds UP.
        self.assertEqual(_months_between(date(2026, 1, 15), date(2026, 3, 20)), 3)

    def test_sub_month_partial_rounds_up_to_one(self):
        # Ten days inside the same month is still a (partial) month -> 1.
        self.assertEqual(_months_between(date(2026, 1, 10), date(2026, 1, 20)), 1)

    def test_day_of_month_clamp_edges_add_the_extra_month(self):
        # The date-bomb: back-date suspendedAt by exactly two months, then resume
        # on a clamp edge. shift_months clamps the back-dated day to the shorter
        # month, so startDate+2mo lands a day BEFORE the resume date and the shift
        # rounds up to 3. These are the Jan-31 / Apr-30 / Aug-31 cases.
        for resume in (date(2026, 1, 31), date(2026, 4, 30), date(2026, 8, 31)):
            suspended_from = shift_months(resume, -2)
            with self.subTest(resume=resume, suspended_from=suspended_from):
                self.assertEqual(_months_between(suspended_from, resume), 3)

    def test_non_clamp_two_month_backdate_stays_two(self):
        # A mid-month resume has no clamp, so the same 2-month back-date is a
        # clean 2 (the case the naive assumption gets right).
        resume = date(2026, 6, 15)
        self.assertEqual(_months_between(shift_months(resume, -2), resume), 2)

    def test_accepts_aware_datetime_and_iso_string_inputs(self):
        # _months_between coerces stored timestamps / ISO strings, not just dates.
        aware_from = datetime(2026, 1, 15, 17, 0, tzinfo=timezone.utc)
        self.assertEqual(_months_between(aware_from, date(2026, 3, 15)), 2)
        self.assertEqual(_months_between("2026-01-15", "2026-03-20"), 3)
