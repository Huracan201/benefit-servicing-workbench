"""Data-access gateways (repositories) for the BenefitServicing Workbench.

Thin, typed Firestore accessors for the Phase-2 command layer (specs/19 §19.2).
Each entity module exposes ``ref``/``get`` (+ per-entity queries); ``refs`` owns
the collection-name constants (specs/04 §4.1) and the generic
``doc``/``get``/``new_doc``/``stamp_create``/``stamp_update`` helpers. **No
business logic** — commands compose these inside their transactions.

Import per-entity modules directly, e.g.::

    from repositories import contributions, refs
    from repositories import stamp_create, stamp_update
"""

from __future__ import annotations

from . import (
    agreements,
    attempts,
    borrowers,
    contributions,
    employers,
    idempotency_keys,
    loans,
    operational_exceptions,
    refs,
    servicing_events,
    simulated_charges,
    users,
)
from .refs import (
    doc,
    field_filter,
    get,
    new_doc,
    snapshot_to_dict,
    stamp_create,
    stamp_update,
    stream_to_dicts,
)

__all__ = [
    # submodules
    "refs",
    "employers",
    "borrowers",
    "loans",
    "agreements",
    "contributions",
    "attempts",
    "operational_exceptions",
    "idempotency_keys",
    "users",
    "servicing_events",
    "simulated_charges",
    # generic helpers re-exported for convenience
    "doc",
    "get",
    "new_doc",
    "snapshot_to_dict",
    "stream_to_dicts",
    "field_filter",
    "stamp_create",
    "stamp_update",
]
