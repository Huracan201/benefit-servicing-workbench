"""Request-scoped correlation-id plumbing + defense-in-depth response headers.

specs/16 §16.4 (correlation id) and specs/12 (auth/security). ``correlationId``
ties an API request to the events it appends, the logs it emits, and any task it
enqueues, so one command is traceable end-to-end.
"""

from __future__ import annotations

import re
import uuid
from typing import Callable

from django.http import HttpRequest, HttpResponse

from core.logging_utils import (
    CORRELATION_ID_HEADER,
    CORRELATION_ID_META_KEY,
    reset_correlation_id,
    set_correlation_id,
)

# A correlation id is echoed into the structured logs AND back on the response header, so an
# UNTRUSTED inbound value must be constrained (security review §7 #9): bound its length and
# restrict it to a safe charset, so it can carry no CR/LF or control characters — no log
# forging, no response-header injection. Anything that does not match is discarded and a
# fresh id is minted. The charset covers uuids (hex or dashed) and reasonable caller ids.
_SAFE_CORRELATION_ID = re.compile(r"\A[A-Za-z0-9_.-]{1,128}\Z")


class CorrelationIdMiddleware:
    """Attach a correlation id to every request/response and the log context.

    - An inbound ``X-Correlation-Id`` is honored ONLY if it is well-formed (safe charset,
      ≤128 chars), so a caller — or an ``/internal`` task carrying the originating command's
      id — keeps one id across hops WITHOUT letting an attacker inject arbitrary content into
      the logs or the echoed response header.
    - Absent, blank, or malformed → a ``uuid4`` is minted.
    - The id is set on ``request.correlation_id``, bound to
      ``core.logging_utils.correlation_id_var`` for structured logging, and written back on
      the response header.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        inbound = request.META.get(CORRELATION_ID_META_KEY, "").strip()
        correlation_id = inbound if _SAFE_CORRELATION_ID.match(inbound) else uuid.uuid4().hex

        request.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = self.get_response(request)
        finally:
            reset_correlation_id(token)

        response[CORRELATION_ID_HEADER] = correlation_id
        return response


class SecurityHeadersMiddleware:
    """Defense-in-depth response headers that Django's ``SecurityMiddleware`` does not set for
    a JSON API (specs/12; security review §7 #9). The API serves JSON only — it loads no
    subresources and must never be framed — so the policy is maximally strict:

    - ``X-Frame-Options: DENY`` + a ``Content-Security-Policy`` with ``frame-ancestors 'none'``
      (clickjacking / embedding protection for old and modern browsers).
    - ``default-src 'none'`` / ``base-uri 'none'`` — a JSON response references nothing.

    ``setdefault`` leaves any per-view override intact. (Nosniff, referrer-policy, HSTS, and
    the production SSL redirect are handled by ``django.middleware.security.SecurityMiddleware``
    from the ``SECURE_*`` settings.)
    """

    _CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        response.setdefault("X-Frame-Options", "DENY")
        response.setdefault("Content-Security-Policy", self._CSP)
        return response
