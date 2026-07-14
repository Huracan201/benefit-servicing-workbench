"""Firestore collection constants + generic document-access helpers.

This is the thin data-access foundation for the Phase-2 command layer (specs/19
§19.2). It owns:

- the **exact** collection / subcollection names from specs/04 §4.1 (single
  source of truth so no module hard-codes a string), and
- generic ``doc`` / ``get`` reference+snapshot helpers, plus ``stamp_create`` /
  ``stamp_update`` which set the common document fields (specs/README "Common
  document fields") using ``firestore.SERVER_TIMESTAMP`` and a monotonic
  ``revision`` audit counter.

No business logic lives here. ``google.cloud.firestore`` is imported lazily so
this module (and the whole package) imports cleanly even when the client library
is absent — mirroring ``common.firestore``.
"""

from __future__ import annotations

from typing import Any, Optional

# --------------------------------------------------------------------------- #
# Collection & subcollection names — specs/04 §4.1 (authoritative spelling)
# --------------------------------------------------------------------------- #

# Command-owned entity collections
EMPLOYERS = "employers"
BORROWERS = "borrowers"
LOANS = "loans"
BENEFIT_AGREEMENTS = "benefitAgreements"
SCHEDULED_CONTRIBUTIONS = "scheduledContributions"

# Cross-entity / operational collections
SERVICING_EVENTS = "servicingEvents"
OPERATIONAL_EXCEPTIONS = "operationalExceptions"
IDEMPOTENCY_KEYS = "idempotencyKeys"
SIMULATED_CHARGES = "simulatedCharges"
USERS = "users"

# Subcollections
LOAN_NOTES = "notes"  # loans/{loanId}/notes/{noteId}
LOAN_EVENTS = "events"  # loans/{loanId}/events/{eventId} — event mirror
BORROWER_EVENTS = "events"  # borrowers/{borrowerId}/events/{eventId} — event mirror
ATTEMPTS = "attempts"  # scheduledContributions/{id}/attempts/{attemptId}
EMPLOYER_SUMMARY_PERIODS = "periods"  # employerSummaries/{id}/periods/{YYYY-MM}

# Read models (specs/05) — derived; listed for completeness / projection layer.
PORTFOLIO_SUMMARIES = "portfolioSummaries"
EMPLOYER_SUMMARIES = "employerSummaries"
LOAN_WORKBENCHES = "loanWorkbenches"

# Default schema version stamped on freshly created documents.
SCHEMA_VERSION = 1

# Default page size for cursor-paginated repository reads (e.g.
# ``contributions.due``). Bounds how many documents a fan-out/sweep pulls per
# invocation; the caller pages the remainder via the returned cursor. Distinct
# from the per-transaction write-batch sizes local to the command modules
# (those cap writes per Firestore txn; this caps reads per page).
BATCH_SIZE = 200


# --------------------------------------------------------------------------- #
# Lazy firestore module accessor
# --------------------------------------------------------------------------- #


def _firestore():
    """Return the ``google.cloud.firestore`` module (lazy — see module docstring)."""
    from google.cloud import firestore  # noqa: WPS433 (deliberate lazy import)

    return firestore


def field_filter(field: str, op: str, value):
    """Build a ``FieldFilter`` (the non-deprecated ``where()`` argument form).

    Usage: ``collection.where(filter=refs.field_filter("status", "==", value))``.
    """
    from google.cloud.firestore_v1.base_query import (  # noqa: WPS433
        FieldFilter,
    )

    return FieldFilter(field, op, value)


# --------------------------------------------------------------------------- #
# Snapshot -> dict-with-id
# --------------------------------------------------------------------------- #


def snapshot_to_dict(snapshot) -> Optional[dict[str, Any]]:
    """Return a ``DocumentSnapshot`` as ``dict`` (with ``id``) or ``None``.

    ``None`` is returned for a missing document so callers can branch on
    presence without touching the firestore snapshot API.
    """
    if snapshot is None or not snapshot.exists:
        return None
    data = snapshot.to_dict() or {}
    data["id"] = snapshot.id
    return data


def stream_to_dicts(query) -> list[dict[str, Any]]:
    """Materialize a firestore ``Query`` into a list of dict-with-id documents."""
    return [snapshot_to_dict(snap) for snap in query.stream()]


def get_in_txn(txn, ref) -> Optional[dict[str, Any]]:
    """Read one ``DocumentReference`` *inside* a ``Transaction`` as dict-with-id or ``None``.

    ``Transaction.get`` returns a single snapshot or a one-element generator depending on the
    client version — normalise both, then reuse :func:`snapshot_to_dict`. This is the SINGLE
    home for the transactional-read helper that was previously copy-pasted per module (as
    ``_get_in_txn`` / ``_txn_get`` / ``_read``).
    """
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    return snapshot_to_dict(snap)


# --------------------------------------------------------------------------- #
# Generic reference / read helpers
# --------------------------------------------------------------------------- #


def doc(client, collection: str, doc_id: str):
    """Return the ``DocumentReference`` at ``collection/{doc_id}``."""
    return client.collection(collection).document(doc_id)


def get(client, collection: str, doc_id: str) -> Optional[dict[str, Any]]:
    """Read ``collection/{doc_id}`` and return a dict-with-id, or ``None``."""
    return snapshot_to_dict(doc(client, collection, doc_id).get())


def new_doc(client, collection: str):
    """Return a fresh ``DocumentReference`` with an auto-generated id."""
    return client.collection(collection).document()


# --------------------------------------------------------------------------- #
# Common-field stampers (specs/README "Common document fields")
# --------------------------------------------------------------------------- #


def stamp_create(data: dict[str, Any], actor_id: str) -> dict[str, Any]:
    """Set the create-time common fields on ``data`` in place, and return it.

    Sets ``createdAt``/``updatedAt`` to ``SERVER_TIMESTAMP``,
    ``createdBy``/``updatedBy`` to ``actor_id``, ``revision`` to ``0`` (the audit
    counter starts at zero and increments on each material update), and
    ``schemaVersion`` (unless the caller already supplied one).
    """
    fs = _firestore()
    data["createdAt"] = fs.SERVER_TIMESTAMP
    data["updatedAt"] = fs.SERVER_TIMESTAMP
    data["createdBy"] = actor_id
    data["updatedBy"] = actor_id
    data["revision"] = 0
    data.setdefault("schemaVersion", SCHEMA_VERSION)
    return data


def stamp_update(data: dict[str, Any], actor_id: str) -> dict[str, Any]:
    """Set the update-time common fields on ``data`` in place, and return it.

    Sets ``updatedAt`` to ``SERVER_TIMESTAMP``, ``updatedBy`` to ``actor_id``,
    and bumps ``revision`` atomically via ``firestore.Increment(1)`` so no prior
    read of the counter is required.
    """
    fs = _firestore()
    data["updatedAt"] = fs.SERVER_TIMESTAMP
    data["updatedBy"] = actor_id
    data["revision"] = fs.Increment(1)
    return data
