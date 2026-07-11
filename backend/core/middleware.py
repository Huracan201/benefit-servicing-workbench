"""Request-scoped correlation-id plumbing — specs/16 §16.4.

``correlationId`` ties an API request to the events it appends, the logs it
emits, and any task it enqueues, so one command is traceable end-to-end. This
middleware reads (or mints) the id, exposes it on the request, binds it to the
logging contextvar, and echoes it on the response.
"""

from __future__ import annotations

import uuid
from typing import Callable

from django.http import HttpRequest, HttpResponse

from core.logging_utils import (
    CORRELATION_ID_HEADER,
    CORRELATION_ID_META_KEY,
    reset_correlation_id,
    set_correlation_id,
)


class CorrelationIdMiddleware:
    """Attach a correlation id to every request/response and the log context.

    - Inbound ``X-Correlation-Id`` is honored (so a caller — or an ``/internal``
      task carrying the originating command's id — keeps one id across hops).
    - Absent/blank, a ``uuid4`` is minted.
    - The id is set on ``request.correlation_id``, bound to
      ``core.logging_utils.correlation_id_var`` for structured logging, and
      written back on the response header.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        correlation_id = request.META.get(CORRELATION_ID_META_KEY, "").strip()
        if not correlation_id:
            correlation_id = uuid.uuid4().hex

        request.correlation_id = correlation_id
        token = set_correlation_id(correlation_id)
        try:
            response = self.get_response(request)
        finally:
            reset_correlation_id(token)

        response[CORRELATION_ID_HEADER] = correlation_id
        return response
