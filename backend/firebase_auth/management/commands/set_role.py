"""Break-glass admin command: grant a servicing role to a user.

    python manage.py set_role <email> <ROLE>

Role-granting normally requires an ADMINISTRATOR, which is circular for the very
first administrator (specs/12 §12.3). This management command is the documented
break-glass path, run by an operator with project credentials. It:

1. resolves the Firebase user by email,
2. sets the authoritative ``role`` custom claim via the Admin SDK,
   2b. on a **demotion** (``role_rank(new) < role_rank(previous)``) revokes the
   user's refresh tokens — parity with the runtime ``set_user_role`` command and
   the write-path ``verify_id_token(check_revoked=True)`` (specs/12 §12.3) so the
   reduced privilege takes effect immediately, not after the ~1h ID-token TTL,
3. upserts the ``users/{uid}`` mirror document (via :mod:`common.firestore`),
4. appends an immutable ``USER_ROLE_CHANGED`` servicing event with
   ``actorType: SYSTEM`` (global-only — role changes have no loan/borrower scope,
   specs/04 §4.9 mirroring rule).

The ``ROLE`` argument is validated against the Role enum. Third-party imports are
lazy so the module ``py_compile``s in an offline sandbox.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from firebase_auth.permissions import ROLE_ORDER, role_rank


class Command(BaseCommand):
    help = "Break-glass: set a user's servicing role (custom claim + mirror + event)."

    def add_arguments(self, parser):
        parser.add_argument("email", help="Email of the Firebase user to grant a role.")
        parser.add_argument(
            "role",
            help=f"Role to grant. One of: {', '.join(ROLE_ORDER)}.",
        )

    def handle(self, *args, **options):
        email = options["email"].strip()
        role = options["role"].strip()

        if role not in ROLE_ORDER:
            raise CommandError(
                f"Invalid role {role!r}. Must be one of: {', '.join(ROLE_ORDER)}."
            )

        # --- Lazy imports (offline-safe) -------------------------------
        from firebase_admin import auth as firebase_auth_sdk
        from google.cloud import firestore

        from firebase_auth import admin_init

        admin_init.initialize_app()

        # 1. Resolve user.
        try:
            user = firebase_auth_sdk.get_user_by_email(email)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Could not find Firebase user {email!r}: {exc}")

        uid = user.uid
        previous_role = (user.custom_claims or {}).get("role")

        new_claims = dict(user.custom_claims or {})
        new_claims["role"] = role

        # 2. On a DEMOTION (strictly-lower target rank) revoke refresh tokens BEFORE
        # mutating the claim, so the reduced privilege takes effect immediately
        # (parity with the write-path verify_id_token(check_revoked=True), specs/12
        # §12.3). Revoking first means a failure that forces a re-run still reads the
        # ORIGINAL (higher) role as previous_role and re-revokes, instead of reading
        # the already-lowered claim and silently skipping revocation. Revocation is
        # idempotent; the sub-second window before the claim write is acceptable for
        # a manual break-glass command (the runtime set_user_role persists the
        # original role in its idempotency record to close the same gap). A promotion
        # / lateral / first grant (previous_role None ranks -1) leaves sessions intact.
        demoted = role_rank(role) < role_rank(previous_role)
        if demoted:
            try:
                firebase_auth_sdk.revoke_refresh_tokens(uid)
            except Exception as exc:  # noqa: BLE001
                raise CommandError(
                    f"Failed to revoke refresh tokens for {uid}: {exc}"
                )

        # 3. Set the authoritative custom claim (preserve other claims).
        try:
            firebase_auth_sdk.set_custom_user_claims(uid, new_claims)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(f"Failed to set custom claims for {uid}: {exc}")

        # 3. Upsert the users/{uid} mirror + 4. append the audit event, together.
        from common.firestore import get_client

        client = get_client()
        server_ts = firestore.SERVER_TIMESTAMP
        actor_id = "system:set_role"

        user_ref = client.collection("users").document(uid)
        snapshot = user_ref.get()
        batch = client.batch()  # mirror + audit event commit atomically
        base = {
            "uid": uid,
            "email": user.email or email,
            "displayName": user.display_name or "",
            "role": role,
            "status": "DISABLED" if user.disabled else "ACTIVE",
            "updatedAt": server_ts,
            "updatedBy": actor_id,
            "schemaVersion": 1,
        }
        if snapshot.exists:
            existing = snapshot.to_dict() or {}
            base["revision"] = int(existing.get("revision", 0)) + 1
            batch.set(user_ref, base, merge=True)
        else:
            base["createdAt"] = server_ts
            base["createdBy"] = actor_id
            base["revision"] = 1
            batch.set(user_ref, base)

        event_ref = client.collection("servicingEvents").document()
        batch.set(
            event_ref,
            {
                "eventType": "USER_ROLE_CHANGED",
                "entityType": "USER",
                "entityId": uid,
                "loanId": None,
                "borrowerId": None,
                "employerId": None,
                "benefitAgreementId": None,
                "actorType": "SYSTEM",
                "actorId": actor_id,
                "actorRole": None,
                "actorName": "set_role (break-glass CLI)",
                "correlationId": f"set_role:{uid}",
                "sequence": 1,
                "metadata": {
                    "previousRole": previous_role,
                    "newRole": role,
                    "targetEmail": user.email or email,
                },
                "createdAt": server_ts,
            },
        )
        batch.commit()

        self.stdout.write(
            self.style.SUCCESS(
                f"Set role {role} for {email} (uid={uid}); "
                f"previous role: {previous_role}."
                + (" Refresh tokens revoked (demotion)." if demoted else "")
            )
        )
