"""Operational-exception data-access gateway —
``operationalExceptions/{exceptionId}`` (specs/04 §4.10).

Auto-created exceptions use the deterministic id ``{entityId}__{exceptionType}``
(``common.ids.exception_id``); manually-created ones use auto-ids (``new_ref``).
Own lifecycle timestamps, no ``revision`` (specs/04 §4.12a) — no ``stamp_*``.
"""

from __future__ import annotations

from typing import Any, Optional

from common.ids import exception_id as _exception_id

from . import refs


def ref(client, exception_id: str):
    """``DocumentReference`` for ``operationalExceptions/{exception_id}``."""
    return refs.doc(client, refs.OPERATIONAL_EXCEPTIONS, exception_id)


def ref_for_entity(client, entity_id: str, exception_type):
    """Deterministic ref addressed by ``(entity_id, exception_type)``."""
    return ref(client, _exception_id(entity_id, exception_type))


def new_ref(client):
    """Auto-id ``DocumentReference`` for a manually-created exception."""
    return refs.new_doc(client, refs.OPERATIONAL_EXCEPTIONS)


def get(client, exception_id: str) -> Optional[dict[str, Any]]:
    """Read the exception document as dict-with-id, or ``None``."""
    return refs.get(client, refs.OPERATIONAL_EXCEPTIONS, exception_id)
