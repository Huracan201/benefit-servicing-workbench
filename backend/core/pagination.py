"""DRF pagination bounded to the API contract (specs/11 §11.5, specs/21 §21.1).

`PAGE_SIZE` in settings only sets the *default*; `LimitOffsetPagination` otherwise
honors an arbitrarily large client-supplied `limit`. This caps it at 200.
"""

from rest_framework.pagination import LimitOffsetPagination


class CappedLimitOffsetPagination(LimitOffsetPagination):
    """Limit/offset pagination with a hard ceiling (default 50, max 200)."""

    default_limit = 50
    max_limit = 200
