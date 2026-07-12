"""Idempotency-record data-access gateway — ``idempotencyKeys/{idempotencyKey}``
(specs/04 §4.11).

The id is the client ``Idempotency-Key`` header verbatim. The record is created
**inside** the state-transition transaction with a create-precondition (specs/08
§8.2) — the idempotency service owns that logic; this gateway is address-only.
Own lifecycle timestamps, no ``revision`` (specs/04 §4.12a).
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, key: str):
    """``DocumentReference`` for ``idempotencyKeys/{key}``."""
    return refs.doc(client, refs.IDEMPOTENCY_KEYS, key)


def get(client, key: str) -> Optional[dict[str, Any]]:
    """Read the idempotency record as dict-with-id, or ``None``."""
    return refs.get(client, refs.IDEMPOTENCY_KEYS, key)
