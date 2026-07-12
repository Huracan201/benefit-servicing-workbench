"""core.exception_handler — DRF handler for otherwise-uncaught exceptions.

Views translate *expected* failures into the specs/11 §11.3 error envelope
themselves (via ``CommandError.to_body``). Anything they do **not** catch — a
Firestore outage, a programming error — would otherwise fall through to
Django's default handler and, with ``DEBUG`` on, return a full traceback in the
response body. This handler closes that gap:

* a DRF-recognised exception (validation / auth / throttle / 404 …) keeps its
  normal, already-safe response; and
* any other exception is logged server-side (with stack, to the structured
  logger) and rendered as a generic ``INTERNAL_ERROR`` 500 that leaks nothing —
  independent of ``DEBUG``.

Registered as ``REST_FRAMEWORK["EXCEPTION_HANDLER"]`` (config/settings.py).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

logger = logging.getLogger("bsw.request")


def custom_exception_handler(exc: Exception, context: dict) -> Optional[Response]:
    """Return DRF's response for handled errors; a generic 500 for the rest."""
    response = drf_exception_handler(exc, context)
    if response is not None:
        # DRF already produced a safe, structured response — pass it through.
        return response

    request = context.get("request") if isinstance(context, dict) else None
    correlation_id = getattr(request, "correlation_id", None)
    # exc_info attaches the traceback to the structured log's ``exception``
    # field (server-side only); correlationId is picked up from the request
    # contextvar by the formatter. Nothing sensitive crosses into the body.
    logger.error("unhandled exception in request handler", exc_info=exc)

    error: dict[str, Any] = {
        "code": "INTERNAL_ERROR",
        "message": "internal server error",
    }
    if correlation_id:
        error["correlationId"] = correlation_id
    return Response({"error": error}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
