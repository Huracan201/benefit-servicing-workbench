"""Employer data-access gateway — ``employers/{employerId}`` (specs/04 §4.3)."""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, employer_id: str):
    """``DocumentReference`` for ``employers/{employer_id}``."""
    return refs.doc(client, refs.EMPLOYERS, employer_id)


def get(client, employer_id: str) -> Optional[dict[str, Any]]:
    """Read the employer document as dict-with-id, or ``None``."""
    return refs.get(client, refs.EMPLOYERS, employer_id)
