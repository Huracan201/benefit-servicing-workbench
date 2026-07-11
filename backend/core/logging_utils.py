"""Structured (JSON) logging for the backend — specs/16 §16.2.

Every API request and task execution emits a single-line JSON object carrying
the operational fields an incident/demo needs to trace one command end-to-end:

    correlationId, requestId, userId, userRole, operation, entityType,
    entityId, idempotencyKey (hashed), result, durationMs, errorCode

Design notes (specs/16):
- The ``correlationId`` is propagated via a :class:`contextvars.ContextVar` so a
  log call deep in a handler picks it up without threading it through every
  signature. ``core.middleware.CorrelationIdMiddleware`` sets it per request.
- **Never log full PII or raw tokens.** The formatter emits only a whitelist of
  known operational fields plus the log message; unknown ``extra`` attributes are
  ignored. Idempotency keys are logged **hashed** (:func:`hash_idempotency_key`).

This module is pure Python + stdlib and imports no Django at module load, so it
is safe to import from anywhere (settings, middleware, tasks).
"""

from __future__ import annotations

import contextvars
import datetime as _dt
import hashlib
import json
import logging
from typing import Any

# --------------------------------------------------------------------------- #
# Correlation-id propagation
# --------------------------------------------------------------------------- #

#: Bound per-request by CorrelationIdMiddleware; read by the formatter and by
#: any log call that does not pass ``correlationId`` explicitly.
correlation_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "correlation_id", default=None
)

CORRELATION_ID_HEADER = "X-Correlation-Id"
#: WSGI/Django ``request.META`` key for the inbound header above.
CORRELATION_ID_META_KEY = "HTTP_X_CORRELATION_ID"


def set_correlation_id(correlation_id: str | None) -> contextvars.Token:
    """Bind ``correlation_id`` for the current context; returns a reset token."""
    return correlation_id_var.set(correlation_id)


def get_correlation_id() -> str | None:
    """Return the correlation id bound to the current context, if any."""
    return correlation_id_var.get()


def reset_correlation_id(token: contextvars.Token) -> None:
    """Restore the correlation-id contextvar using a token from :func:`set_correlation_id`."""
    correlation_id_var.reset(token)


# --------------------------------------------------------------------------- #
# Field whitelist & helpers
# --------------------------------------------------------------------------- #

#: The operational fields from specs/16 §16.2. Only these (plus message/level/
#: logger/timestamp) are emitted — this whitelist is what keeps PII/tokens out.
LOG_FIELDS: tuple[str, ...] = (
    "correlationId",
    "requestId",
    "userId",
    "userRole",
    "operation",
    "entityType",
    "entityId",
    "idempotencyKey",
    "result",
    "durationMs",
    "errorCode",
)


def hash_idempotency_key(key: str | None) -> str | None:
    """Return ``sha256:<hex>`` for an idempotency key, or ``None``.

    specs/16 §16.2 requires idempotency keys be logged as a hash, never verbatim.
    """
    if not key:
        return None
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class StructuredLogFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as one line of JSON.

    Operational fields are read from ``record.__dict__`` when supplied via the
    standard ``logging`` ``extra=`` mechanism, e.g.::

        logger.info("payment posted", extra={"operation": "PROCESS_CONTRIBUTION",
                                              "entityId": contribution_id,
                                              "result": "POSTED", "durationMs": 42})

    ``correlationId`` falls back to the contextvar when not passed explicitly.
    """

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - stdlib name
        payload: dict[str, Any] = {
            "timestamp": _dt.datetime.fromtimestamp(
                record.created, tz=_dt.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field in LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value

        if "correlationId" not in payload:
            cid = get_correlation_id()
            if cid is not None:
                payload["correlationId"] = cid

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, default=str, separators=(",", ":"))


def log_event(
    logger: logging.Logger,
    level: int,
    message: str,
    **fields: Any,
) -> None:
    """Emit a structured log line, keeping only whitelisted operational fields.

    Convenience over ``logger.log(level, msg, extra=...)`` that (a) drops unknown
    keys so a caller cannot accidentally leak PII into ``extra`` and (b) hashes
    ``idempotencyKey`` if a raw one is passed.
    """
    extra: dict[str, Any] = {}
    for key, value in fields.items():
        if key not in LOG_FIELDS:
            continue
        if key == "idempotencyKey":
            value = hash_idempotency_key(value)
        extra[key] = value
    logger.log(level, message, extra=extra)


def get_logging_config(level: str = "INFO") -> dict[str, Any]:
    """Return a ``logging.config.dictConfig`` dict wiring :class:`StructuredLogFormatter`.

    Provided as a seam for the Django settings module (``LOGGING = get_logging_config()``);
    ``core`` does not apply it itself.
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "json": {
                "()": "core.logging_utils.StructuredLogFormatter",
            },
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "formatter": "json",
            },
        },
        "root": {
            "handlers": ["console"],
            "level": level,
        },
    }
