"""User data-access gateway — ``users/{uid}`` (specs/04 §4.12).

``role`` mirrors the Firebase custom claim (the claim is authoritative). Client
writes to this doc are denied by security rules; role changes go through an admin
command that sets the claim and updates this mirror.
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, uid: str):
    """``DocumentReference`` for ``users/{uid}``."""
    return refs.doc(client, refs.USERS, uid)


def get(client, uid: str) -> Optional[dict[str, Any]]:
    """Read the user document as dict-with-id, or ``None``."""
    return refs.get(client, refs.USERS, uid)
