"""administration.services — the two ADMINISTRATOR-only commands (specs/11 §11.4).

* :func:`set_user_role` — ``POST /admin/users/{uid}/role`` (specs/12 §12.3): the
  *sole* runtime way to change a user's role. It sets the authoritative Firebase
  ``role`` custom claim via the Admin SDK, upserts the ``users/{uid}`` mirror
  document, and appends a ``USER_ROLE_CHANGED`` servicing event — mirroring the
  break-glass ``set_role`` management command, but user-authorized (ADMIN) and
  idempotency-guarded.
* :func:`set_employer_status` — ``POST /admin/employers/{employerId}/status``
  (specs/06 §6.6a, specs/11 §11.4): flips an employer ``ACTIVE ⇄ INACTIVE`` via
  the employer state machine and appends an ``EMPLOYER_STATUS_CHANGED`` event.
  ``INACTIVE`` gates *new* benefit activations only (specs/10 §10.1) — existing
  benefits keep processing.

Both follow the reference idempotency-first ordering of
:func:`benefits.services.activate_benefit`: reads → ``idempotency.begin`` →
handle replay/in-progress/reuse → validate/transition → writes → events →
``idempotency.complete``, all inside one ``@transactional`` function.

The Firebase custom-claim write in :func:`set_user_role` is not a Firestore
operation, so — like the payment adapter charge in :mod:`payments.service` — it
runs *outside* the transaction, in that module's **two-phase** shape: Phase 1
opens the idempotency record (PENDING); Phase 2 sets the claim on the ``NEW``/
proceed path **only**; Phase 3 writes the ``users/{uid}`` mirror + event and
completes the record. Gating the claim write behind a ``NEW`` outcome keeps it
inside the idempotency envelope, so a *replay* returns the stored result **without
re-issuing it** (setting a role claim is still naturally idempotent, so a
post-crash lease reclaim that re-asserts the same claim remains harmless). All
``firebase_admin`` / ``google.cloud`` imports are lazy so this module
``py_compile``s in an offline sandbox.
"""

from __future__ import annotations

from typing import Any, Optional

from commands.base import (
    CommandContext,
    CommandError,
    IdempotencyKeyReused,
    LEASE_TTL_SECONDS,
    NotFound,
    OperationInProgress,
    RETRY_AFTER_IN_PROGRESS,
    ValidationError,
    from_domain_error,
    transactional,
)
from common import errors as domain_errors
from common import state_machines
from common.enums import EmployerStatus
from firebase_auth.permissions import ROLE_ORDER
from idempotency import service as idempotency
from repositories import employers, stamp_create, stamp_update, users
from servicing import events as servicing_events

OPERATION_ROLE = "set-user-role"
OPERATION_EMPLOYER_STATUS = "set-employer-status"

ENTITY_USER = "USER"
ENTITY_EMPLOYER = "EMPLOYER"

_VALID_EMPLOYER_STATUSES = frozenset(
    {EmployerStatus.ACTIVE.value, EmployerStatus.INACTIVE.value}
)


# --------------------------------------------------------------------------- #
# Transactional read helper (mirrors benefits/payments services)
# --------------------------------------------------------------------------- #
def _txn_get(txn: Any, ref: Any) -> Optional[dict]:
    """Read a single document *inside* the transaction, as dict-with-id or None."""
    got = txn.get(ref)
    snap = got if hasattr(got, "exists") else next(iter(got), None)
    if snap is None or not getattr(snap, "exists", False):
        return None
    data = snap.to_dict() or {}
    data["id"] = snap.id
    return data


# --------------------------------------------------------------------------- #
# POST /admin/users/{uid}/role  (specs/12 §12.3)
# --------------------------------------------------------------------------- #
def set_user_role(
    *, uid: str, role: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Set a user's servicing role: Firebase claim + ``users/{uid}`` mirror + event.

    The role is validated against the closed role hierarchy; an unknown value is a
    ``400``. A missing Firebase user is a ``404``. Idempotency uses the
    :mod:`payments.service` two-phase split so the authoritative custom-claim write
    sits INSIDE the idempotency envelope: Phase 1 opens the record (replay → stored
    result, live lease → ``202``, same key/different body → ``409``); the claim is
    set only on a ``NEW`` outcome (never on replay); Phase 3 writes the mirror +
    event and completes the record.
    """
    if not isinstance(role, str):
        raise ValidationError("role must be a string", code="INVALID_ROLE")
    role = role.strip()
    if role not in ROLE_ORDER:
        raise ValidationError(
            f"invalid role {role!r}; must be one of: {', '.join(ROLE_ORDER)}",
            code="INVALID_ROLE",
        )

    if client is None:
        from common.firestore import get_client

        client = get_client()

    # --- resolve the Firebase user (a READ — safe before idempotency.begin) ----
    # firebase_admin is imported lazily so this module py_compiles offline; the
    # SDK is emulator-aware via admin_init (honours FIREBASE_AUTH_EMULATOR_HOST).
    from firebase_admin import auth as firebase_auth_sdk

    from firebase_auth import admin_init

    admin_init.initialize_app()

    try:
        fb_user = firebase_auth_sdk.get_user(uid)
    except firebase_auth_sdk.UserNotFoundError as exc:
        # ONLY "no such user" is a 404. Any other Auth error (emulator down,
        # permission, transient outage) must propagate as a retryable 5xx rather
        # than be misreported as a missing user (mirrors the seed provisioner's
        # narrow UserNotFoundError catch).
        raise NotFound(f"firebase user {uid!r} not found") from exc

    previous_role = (fb_user.custom_claims or {}).get("role")
    target_email = fb_user.email or ""
    display_name = fb_user.display_name or ""
    user_status = "DISABLED" if fb_user.disabled else "ACTIVE"
    # Preserve any other custom claims already present; the actual claim WRITE is
    # gated behind a NEW idempotency outcome below (Phase 2), never on a replay.
    new_claims = dict(fb_user.custom_claims or {})
    new_claims["role"] = role

    # The response body (also stored on the idempotency record for replay).
    result = {
        "uid": uid,
        "role": role,
        "previousRole": previous_role,
        "email": target_email,
        "displayName": display_name,
        "status": user_status,
        "correlationId": ctx.correlation_id,
    }

    # --- Phase 1 (transaction): open (or replay) the idempotency record --------
    # Mirrors payments.service's two-phase split: begin writes PENDING so the
    # authoritative external side effect (the claim write) can sit BETWEEN begin
    # and complete. A non-NEW outcome short-circuits (replay/202/409) and the claim
    # is never touched (specs/08 §8.2).
    def _phase1_begin(txn: Any):
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_ROLE,
            request_hash=ctx.request_hash,
            entity_id=uid,
            entity_type=ENTITY_USER,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return ("REPLAY", outcome.result or {})
        if outcome.is_in_progress:
            raise OperationInProgress(
                "user role change already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={"uid": uid},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )
        return ("PROCEED", None)

    # --- Phase 3 (transaction): upsert the mirror + event + complete -----------
    def _phase3_finalize(txn: Any) -> dict:
        # Read the mirror inside THIS txn (before any write) to choose create vs
        # merge — a create stamps createdAt/revision=0, a merge bumps revision.
        existing = _txn_get(txn, users.ref(client, uid))

        mirror: dict[str, Any] = {
            "uid": uid,
            "email": target_email,
            "displayName": display_name,
            "role": role,
            "status": user_status,
        }
        if existing is None:
            stamp_create(mirror, ctx.actor_id)
            txn.set(users.ref(client, uid), mirror)
        else:
            stamp_update(mirror, ctx.actor_id)
            txn.set(users.ref(client, uid), mirror, merge=True)

        # --- audit event (global-only scope: role changes have no loan/borrower)
        servicing_events.append(
            txn,
            event_type="USER_ROLE_CHANGED",
            entity_type=ENTITY_USER,
            entity_id=uid,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "previousRole": previous_role,
                "newRole": role,
                "targetEmail": target_email,
            },
        )

        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    try:
        kind, replay = transactional(client)(_phase1_begin)()
        if kind == "REPLAY":
            return replay

        # --- Phase 2 (NO transaction): set the authoritative Firebase claim ----
        # Proceed path only (a NEW outcome) — never on replay, so the claim is not
        # re-written outside the idempotency envelope. The write is naturally
        # idempotent, so a post-crash lease reclaim (same request) safely
        # re-asserts the same claim before Phase 3 re-runs.
        try:
            firebase_auth_sdk.set_custom_user_claims(uid, new_claims)
        except ValueError as exc:
            # Invalid claims payload — a client/input problem -> 400.
            raise ValidationError(
                f"invalid claims for {uid!r}: {exc}", code="INVALID_CLAIMS"
            ) from exc
        # A FirebaseError / transient Auth outage is a backend failure: let it
        # propagate as a retryable 5xx rather than masking it as a 409 (a wrapped
        # DomainError). specs/16: don't turn outages into client-error codes.

        return transactional(client)(_phase3_finalize)()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc


# --------------------------------------------------------------------------- #
# POST /admin/employers/{employerId}/status  (specs/06 §6.6a, specs/11 §11.4)
# --------------------------------------------------------------------------- #
def set_employer_status(
    *, employer_id: str, status: str, ctx: CommandContext, client: Any = None
) -> dict:
    """Flip an employer ``ACTIVE ⇄ INACTIVE`` via the employer state machine.

    An unknown status value is a ``400``; a missing employer is a ``404``; an
    illegal edge (e.g. ``ACTIVE`` → ``ACTIVE``) surfaces as ``409
    INVALID_TRANSITION`` from :func:`assert_transition`. Idempotency matches the
    reference command ordering.
    """
    status = (status or "").strip()
    if status not in _VALID_EMPLOYER_STATUSES:
        raise ValidationError(
            f"invalid status {status!r}; must be ACTIVE or INACTIVE",
            code="INVALID_EMPLOYER_STATUS",
        )

    if client is None:
        from common.firestore import get_client

        client = get_client()

    @transactional(client)
    def _run(txn: Any) -> dict:
        # --- reads (all before any write) ------------------------------------
        employer = _txn_get(txn, employers.ref(client, employer_id))
        if employer is None:
            raise NotFound(f"employer {employer_id!r} not found")

        # --- idempotency: begin inside the txn -------------------------------
        outcome = idempotency.begin(
            txn,
            key=ctx.idempotency_key,
            operation=OPERATION_EMPLOYER_STATUS,
            request_hash=ctx.request_hash,
            entity_id=employer_id,
            entity_type=ENTITY_EMPLOYER,
            lease_ttl_seconds=LEASE_TTL_SECONDS,
            lease_owner=ctx.lease_owner,
            client=client,
        )
        if outcome.is_replay:
            return outcome.result or {}
        if outcome.is_in_progress:
            raise OperationInProgress(
                "employer status change already in progress",
                retry_after=RETRY_AFTER_IN_PROGRESS,
                state={"employerId": employer_id, "status": employer.get("status")},
            )
        if outcome.is_reuse:
            raise IdempotencyKeyReused(
                "idempotency key reused with a different request"
            )

        # --- transition (a raise aborts the txn, discarding the PENDING key) --
        previous_status = employer.get("status")
        state_machines.assert_transition("employer", previous_status, status)

        # --- write the new status --------------------------------------------
        employer_update = {"status": status}
        stamp_update(employer_update, ctx.actor_id)
        txn.update(employers.ref(client, employer_id), employer_update)

        # --- audit event -----------------------------------------------------
        servicing_events.append(
            txn,
            event_type="EMPLOYER_STATUS_CHANGED",
            entity_type=ENTITY_EMPLOYER,
            entity_id=employer_id,
            actor_id=ctx.actor_id,
            actor_role=ctx.actor_role,
            actor_name=ctx.actor_name,
            correlation_id=ctx.correlation_id,
            sequence=1,
            metadata={
                "previousStatus": previous_status,
                "newStatus": status,
            },
            employer_id=employer_id,
        )

        result = {
            "employerId": employer_id,
            "status": status,
            "previousStatus": previous_status,
            "correlationId": ctx.correlation_id,
        }
        idempotency.complete(txn, ctx.idempotency_key, result, client=client)
        return result

    try:
        return _run()
    except CommandError:
        raise
    except domain_errors.DomainError as exc:
        raise from_domain_error(exc) from exc
