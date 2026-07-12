"""Simulated payment adapter — specs/09 §9.5, specs/08 §8.4.

The adapter is swappable. ``SimulatedPaymentAdapter`` is the MVP implementation;
``PaymentAdapter`` is the interface a real processor would implement.

Why this file exists / the two hard requirements it satisfies (specs/08 §8.4):

1. ``charge`` is idempotent on ``processor_idempotency_key`` — the *same* key
   never double-charges; a replay returns the *same* result.
2. ``get_status`` is a status/query endpoint keyed by that same id, and on an
   UNKNOWN key it atomically writes a **tombstone that FENCES the key**. A later
   ``charge`` with a fenced key is rejected (``NOT_SUBMITTED``). This is what
   turns the reconciliation sweeper's ``NOT_FOUND`` branch into a *durable
   verdict* rather than a race against a still-in-flight charge.

Persistence is Firestore at ``simulatedCharges/{processorIdempotencyKey}``
(client-invisible; deny-all rules like ``idempotencyKeys``). It is deliberately
NOT in-memory: the reconciliation sweeper runs on a different Cloud Run instance
than the one that charged, so an in-memory ledger would pass every local test and
silently break the deployed demo.

Outcomes are scripted deterministically from the seed-only
``contribution.simulatedOutcome`` field, passed through ``charge(metadata=…)``.
Default outcome is success.

This module imports cleanly even when ``google.cloud.firestore`` is absent — the
package import is lazy (inside methods), mirroring ``common.firestore``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable

from common.enums import PaymentFailureCode

__all__ = [
    "ChargeResult",
    "StatusResult",
    "PaymentAdapter",
    "SimulatedPaymentAdapter",
    "SimulatedCrash",
    "SIMULATED_CHARGES_COLLECTION",
    "crash_after_charge",
    "set_crash_after_charge",
    "is_crash_after_charge",
]

# Collection holding the simulator's private charge ledger (specs/04 §4.1,
# specs/09 §9.5). Client-invisible: deny-all security rules, like idempotencyKeys.
SIMULATED_CHARGES_COLLECTION = "simulatedCharges"

# Internal persisted-status values (stored on the ledger doc). Distinct from the
# ChargeResult/StatusResult wire values because a fenced tombstone is stored as
# FENCED but surfaced to get_status callers as NOT_FOUND.
_STATUS_SUCCEEDED = "SUCCEEDED"
_STATUS_FAILED = "FAILED"
_STATUS_FENCED = "FENCED"  # tombstone written by get_status on an unknown key


# --------------------------------------------------------------------------- #
# Result dataclasses (specs/09 §9.5)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class ChargeResult:
    """Outcome of a ``charge`` call.

    ``status`` is ``"SUCCEEDED"`` or ``"FAILED"``. ``processor_reference`` is set
    on success; ``failure_code``/``failure_reason`` are set on failure.
    """

    status: str  # "SUCCEEDED" | "FAILED"
    processor_reference: Optional[str] = None
    failure_code: Optional[PaymentFailureCode] = None
    failure_reason: Optional[str] = None


@dataclass(frozen=True)
class StatusResult:
    """Outcome of a ``get_status`` query.

    ``status`` is ``"SUCCEEDED"``, ``"FAILED"`` or ``"NOT_FOUND"``. An
    INDETERMINATE outcome is **not** a value here — it is the call itself raising
    (transport/5xx); callers treat the raised exception as indeterminate (§9.4).
    """

    status: str  # "SUCCEEDED" | "FAILED" | "NOT_FOUND"
    processor_reference: Optional[str] = None
    failure_code: Optional[PaymentFailureCode] = None
    failure_reason: Optional[str] = None


# --------------------------------------------------------------------------- #
# "Crash after charge, before finalize" test toggle (specs/09 §9.5, 17 §17.2)
# --------------------------------------------------------------------------- #
class SimulatedCrash(RuntimeError):
    """Raised by ``charge`` when the crash toggle is armed.

    Models the process dying after Phase 2 (the charge has been *persisted* to
    the ledger) but before Phase 3 (finalize) commits. The ledger row survives,
    so a later ``get_status`` reconciliation call sees the real outcome — exactly
    the gap the sweeper is designed to close (specs/08 §8.4).
    """


# Module-level flag. When true, a *fresh* charge persists its ledger row and then
# raises SimulatedCrash instead of returning — the caller never finalizes.
_CRASH_AFTER_CHARGE = False


def set_crash_after_charge(value: bool) -> None:
    """Arm/disarm the 'crash after charge, before finalize' toggle."""
    global _CRASH_AFTER_CHARGE
    _CRASH_AFTER_CHARGE = bool(value)


def is_crash_after_charge() -> bool:
    """Return whether the crash toggle is currently armed."""
    return _CRASH_AFTER_CHARGE


@contextlib.contextmanager
def crash_after_charge(enabled: bool = True):
    """Context manager arming the crash toggle for its body.

    Usage::

        with crash_after_charge():
            adapter.charge(...)   # persists, then raises SimulatedCrash
    """
    global _CRASH_AFTER_CHARGE
    previous = _CRASH_AFTER_CHARGE
    _CRASH_AFTER_CHARGE = bool(enabled)
    try:
        yield
    finally:
        _CRASH_AFTER_CHARGE = previous


# --------------------------------------------------------------------------- #
# Scripted-outcome resolution
# --------------------------------------------------------------------------- #
_SUCCESS_TOKENS = frozenset({"", "SUCCEEDED", "SUCCESS", "OK", "POSTED"})


def _resolve_scripted_outcome(metadata: Optional[dict]) -> Optional[PaymentFailureCode]:
    """Map ``metadata['simulatedOutcome']`` to a failure code (or None for success).

    * absent / None / a success token  -> None (charge SUCCEEDS)
    * a valid ``PaymentFailureCode``    -> that code (charge FAILS)
    * any other non-empty string        -> ``SERVICER_UNAVAILABLE`` (generic fail)

    ``simulatedOutcome`` is a seed-only field (specs/04 §4.12a); domain logic
    never reads it — only this simulator does, via the charge metadata.
    """
    raw = None
    if metadata:
        raw = metadata.get("simulatedOutcome")
    if raw is None:
        return None
    token = str(raw).strip().upper()
    if token in _SUCCESS_TOKENS:
        return None
    try:
        return PaymentFailureCode(token)
    except ValueError:
        # Unrecognised non-success script -> a generic transient failure rather
        # than silently succeeding (fail-closed for demo/test intent).
        return PaymentFailureCode.SERVICER_UNAVAILABLE


_FAILURE_REASONS = {
    PaymentFailureCode.SERVICER_UNAVAILABLE: "Downstream servicer unavailable",
    PaymentFailureCode.SERVICER_TIMEOUT: "Downstream servicer timed out",
    PaymentFailureCode.INSUFFICIENT_FUNDS: "Funding account has insufficient funds",
    PaymentFailureCode.ACCOUNT_FROZEN: "Account is frozen at the servicer",
    PaymentFailureCode.INVALID_ACCOUNT: "Invalid loan/account reference",
    PaymentFailureCode.NOT_SUBMITTED: "Charge never reached the processor",
}


def _failure_reason(code: PaymentFailureCode) -> str:
    return _FAILURE_REASONS.get(code, "Payment failed")


def _require_cents(value: int) -> int:
    """Return ``value`` unchanged iff it is a non-negative, non-bool ``int`` (cents).

    Mirrors the integer-cent money discipline in :mod:`common.money`: reject
    floats / strings / bools and negatives outright instead of silently
    truncating (``int(100.99) == 100``) or coercing types. Never mutates the
    value — the exact amount the caller passed is what is charged and persisted
    (specs/07).
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("amount_cents must be an int (cents)")
    if value < 0:
        raise ValueError("amount_cents must be >= 0")
    return value


# --------------------------------------------------------------------------- #
# Adapter interface
# --------------------------------------------------------------------------- #
@runtime_checkable
class PaymentAdapter(Protocol):
    """The interface a real processor would implement (specs/09 §9.5)."""

    def charge(
        self,
        *,
        processor_idempotency_key: str,
        amount_cents: int,
        currency: str,
        metadata: dict,
    ) -> ChargeResult:
        """Idempotent charge. Same key => same charge, never double-charges.

        A key previously FENCED by ``get_status`` is rejected with
        ``FAILED(NOT_SUBMITTED)``.
        """
        ...

    def get_status(self, *, processor_idempotency_key: str) -> StatusResult:
        """Return the charge's outcome.

        On an UNKNOWN key, atomically records a tombstone (FENCES the key) and
        returns ``NOT_FOUND`` — a later ``charge`` with that key must fail. This
        is what makes the sweeper's ``NOT_FOUND`` branch a durable verdict rather
        than a double-charge race (specs/08 §8.4).
        """
        ...


# --------------------------------------------------------------------------- #
# Simulated implementation
# --------------------------------------------------------------------------- #
class SimulatedPaymentAdapter:
    """Firestore-backed payment simulator (specs/09 §9.5).

    Constructed with a ``google.cloud.firestore`` client. All state lives in the
    ``simulatedCharges`` collection so it survives across Cloud Run instances.
    """

    def __init__(self, client) -> None:
        self._client = client

    # -- helpers ---------------------------------------------------------- #
    def _doc_ref(self, key: str):
        return self._client.collection(SIMULATED_CHARGES_COLLECTION).document(key)

    @staticmethod
    def _processor_reference(key: str) -> str:
        return f"sim_ref_{key}"

    @staticmethod
    def _charge_result_from_doc(data: dict) -> ChargeResult:
        status = data.get("status")
        if status == _STATUS_SUCCEEDED:
            return ChargeResult(
                status="SUCCEEDED",
                processor_reference=data.get("processorReference"),
            )
        # FAILED (or a FENCED tombstone surfaced as a NOT_SUBMITTED failure)
        code = data.get("failureCode")
        failure_code = PaymentFailureCode(code) if code else None
        return ChargeResult(
            status="FAILED",
            failure_code=failure_code,
            failure_reason=data.get("failureReason"),
        )

    # -- charge ----------------------------------------------------------- #
    def charge(
        self,
        *,
        processor_idempotency_key: str,
        amount_cents: int,
        currency: str,
        metadata: dict,
    ) -> ChargeResult:
        from google.cloud import firestore  # lazy — see module docstring.

        doc_ref = self._doc_ref(processor_idempotency_key)
        # Reject non-integer / bool / negative cents up front (fail fast, before
        # any ledger write) — never truncate or coerce the amount (specs/07).
        _require_cents(amount_cents)
        failure_code = _resolve_scripted_outcome(metadata)

        # Precompute the row we would write for a fresh charge.
        if failure_code is None:
            fresh_doc = {
                "processorIdempotencyKey": processor_idempotency_key,
                "status": _STATUS_SUCCEEDED,
                "processorReference": self._processor_reference(
                    processor_idempotency_key
                ),
                "failureCode": None,
                "failureReason": None,
                "amountCents": amount_cents,
                "currency": currency,
                "simulatedOutcome": (metadata or {}).get("simulatedOutcome"),
                "chargedAt": firestore.SERVER_TIMESTAMP,
            }
        else:
            fresh_doc = {
                "processorIdempotencyKey": processor_idempotency_key,
                "status": _STATUS_FAILED,
                "processorReference": None,
                "failureCode": str(failure_code),
                "failureReason": _failure_reason(failure_code),
                "amountCents": amount_cents,
                "currency": currency,
                "simulatedOutcome": (metadata or {}).get("simulatedOutcome"),
                "chargedAt": firestore.SERVER_TIMESTAMP,
            }

        @firestore.transactional
        def _txn(txn):
            snapshot = doc_ref.get(transaction=txn)
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                if data.get("status") == _STATUS_FENCED:
                    # Key fenced by a prior get_status: the charge can never
                    # happen. Reject — do NOT write, do NOT double-charge.
                    return (
                        ChargeResult(
                            status="FAILED",
                            failure_code=PaymentFailureCode.NOT_SUBMITTED,
                            failure_reason=(
                                "Idempotency key was fenced by get_status; "
                                "charge rejected (08 §8.4)"
                            ),
                        ),
                        False,  # not a fresh charge
                    )
                # Idempotent replay: return the stored outcome unchanged.
                return (self._charge_result_from_doc(data), False)

            # Fresh charge: persist the ledger row, then return the result.
            txn.set(doc_ref, fresh_doc)
            return (self._charge_result_from_doc(fresh_doc), True)

        result, is_fresh = _txn(self._client.transaction())

        # Crash toggle: the row is now durably committed. Simulate the process
        # dying before finalize — the caller never sees `result` (specs/08 §8.4).
        if is_fresh and _CRASH_AFTER_CHARGE:
            raise SimulatedCrash(
                "Simulated crash after charge, before finalize "
                f"(key={processor_idempotency_key})"
            )

        return result

    # -- get_status ------------------------------------------------------- #
    def get_status(self, *, processor_idempotency_key: str) -> StatusResult:
        from google.cloud import firestore  # lazy — see module docstring.

        doc_ref = self._doc_ref(processor_idempotency_key)

        tombstone = {
            "processorIdempotencyKey": processor_idempotency_key,
            "status": _STATUS_FENCED,
            "processorReference": None,
            "failureCode": str(PaymentFailureCode.NOT_SUBMITTED),
            "failureReason": "get_status fenced an unknown key (08 §8.4)",
            "fencedAt": firestore.SERVER_TIMESTAMP,
        }

        @firestore.transactional
        def _txn(txn):
            snapshot = doc_ref.get(transaction=txn)
            if snapshot.exists:
                data = snapshot.to_dict() or {}
                status = data.get("status")
                if status == _STATUS_SUCCEEDED:
                    return StatusResult(
                        status="SUCCEEDED",
                        processor_reference=data.get("processorReference"),
                    )
                if status == _STATUS_FAILED:
                    code = data.get("failureCode")
                    return StatusResult(
                        status="FAILED",
                        failure_code=PaymentFailureCode(code) if code else None,
                        failure_reason=data.get("failureReason"),
                    )
                # Already fenced (tombstone): a durable NOT_FOUND verdict.
                return StatusResult(
                    status="NOT_FOUND",
                    failure_code=PaymentFailureCode.NOT_SUBMITTED,
                    failure_reason=data.get("failureReason"),
                )

            # UNKNOWN key: atomically FENCE it, then report NOT_FOUND. Any later
            # charge(key) will now be rejected — closing the double-charge race.
            txn.set(doc_ref, tombstone)
            return StatusResult(
                status="NOT_FOUND",
                failure_code=PaymentFailureCode.NOT_SUBMITTED,
                failure_reason="Charge never reached the processor; key fenced",
            )

        return _txn(self._client.transaction())
