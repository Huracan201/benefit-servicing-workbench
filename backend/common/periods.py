"""Calendar / period helpers keyed on the business timezone (specs/README, 07).

SYSTEM_TIMEZONE (default America/New_York, overridable via env SYSTEM_TIMEZONE)
is the business calendar for:
  - deriving period labels 'YYYY-MM' for schedules and monthly aggregates, and
  - computing scheduledDate = 12:00 in SYSTEM_TIMEZONE on the due day
    (noon avoids midnight/DST edge cases).

Pure stdlib (os + datetime + zoneinfo); no django, no google.cloud.
"""

import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

SYSTEM_TIMEZONE_NAME = os.environ.get("SYSTEM_TIMEZONE", "America/New_York")
SYSTEM_TIMEZONE = ZoneInfo(SYSTEM_TIMEZONE_NAME)

# scheduledDate is set at noon local time on the due day.
SCHEDULED_TIME = time(hour=12, minute=0, second=0, microsecond=0)


def _to_system_tz(dt: datetime) -> datetime:
    """Return dt as an aware datetime in SYSTEM_TIMEZONE.

    A naive datetime is interpreted as already being wall-clock time in
    SYSTEM_TIMEZONE; an aware datetime (e.g. a UTC Firestore Timestamp) is
    converted.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=SYSTEM_TIMEZONE)
    return dt.astimezone(SYSTEM_TIMEZONE)


def period_label(dt: datetime) -> str:
    """Return the 'YYYY-MM' period label for `dt` in SYSTEM_TIMEZONE.

    Never derive a period from a raw UTC date elsewhere — always go through here.
    """
    local = _to_system_tz(dt)
    return f"{local.year:04d}-{local.month:02d}"


def shift_months(d: date, months: int) -> date:
    """Return `d` shifted by `months` calendar months, preserving day-of-month.

    Day-of-month is clamped to the last valid day of the target month
    (e.g. Jan 31 + 1 month -> Feb 28/29). `months` may be negative.
    """
    if not isinstance(months, int) or isinstance(months, bool):
        raise TypeError("months must be an int")
    zero_based = (d.month - 1) + months
    year = d.year + zero_based // 12
    month = zero_based % 12 + 1
    # Clamp day to the last day of the target month.
    if month == 12:
        first_of_next = date(year + 1, 1, 1)
    else:
        first_of_next = date(year, month + 1, 1)
    last_day = (first_of_next - date(year, month, 1)).days
    day = min(d.day, last_day)
    return date(year, month, day)


def scheduled_datetime(start_date: date, installment_number: int) -> datetime:
    """Return the aware scheduledDate for an installment.

    Installment 1 is due in the start month; installment n is due (n-1) months
    later on the same day-of-month, at 12:00 in SYSTEM_TIMEZONE (specs/README,
    specs/07). Deterministic given (start_date, installment_number).
    """
    if not isinstance(installment_number, int) or isinstance(installment_number, bool):
        raise TypeError("installment_number must be an int")
    if installment_number < 1:
        raise ValueError("installment_number must be >= 1")
    due_day = shift_months(start_date, installment_number - 1)
    return datetime.combine(due_day, SCHEDULED_TIME, tzinfo=SYSTEM_TIMEZONE)
