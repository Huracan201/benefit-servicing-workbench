"""Demo user provisioning (specs/18 §18.1, specs/12 §12.3).

Creates the three pinned demo accounts — ``ops@demo.test``, ``mgr@demo.test``,
``admin@demo.test`` — used verbatim by Playwright and the demo script, each with
a shared fixed password and the correct **role custom claim** so login-as-role
works. Emulator-aware via :mod:`firebase_auth.admin_init` (which honours
``FIREBASE_AUTH_EMULATOR_HOST``). Also upserts each ``users/{uid}`` mirror doc
(specs/04 §4.12) — the authoritative role is the claim, the doc mirrors it.

Idempotent: an existing user is looked up by email and updated (password + claim
reset every run) rather than duplicated. ``firebase_admin`` is imported lazily so
this module ``py_compile``s offline.
"""

from __future__ import annotations

import os

from common.enums import Role

# Shared fixed password for all demo users (specs/18 §18.1). Overridable via env
# for a non-public deployment; the default is the pinned demo credential.
DEFAULT_PASSWORD = os.environ.get("SEED_DEMO_PASSWORD", "DemoPass!234")

# (email, displayName, role) — one per role.
DEMO_USERS = [
    ("ops@demo.test", "Ops User", Role.OPERATIONS_USER),
    ("mgr@demo.test", "Manager User", Role.SERVICING_MANAGER),
    ("admin@demo.test", "Admin User", Role.ADMINISTRATOR),
]

ACTOR_ID = "system:seed_demo"


def provision_demo_users(client, *, password: str = DEFAULT_PASSWORD) -> list[str]:
    """Create/update the three demo users + role claims + mirror docs.

    Returns the list of ``"email -> ROLE"`` strings provisioned. Raises on a
    hard failure (missing ``firebase_admin`` / unreachable Auth emulator) so the
    caller can surface it.
    """
    from firebase_admin import auth as fb_auth

    from firebase_auth import admin_init

    admin_init.initialize_app()
    server_ts = _server_ts()
    provisioned: list[str] = []

    for email, display_name, role in DEMO_USERS:
        role_str = str(role)
        try:
            user = fb_auth.get_user_by_email(email)
        except fb_auth.UserNotFoundError:
            # Only "no such user" falls through to create_user; any other error
            # (Auth emulator down, permission, misconfiguration) must propagate
            # rather than be silently mistaken for a missing user.
            user = None

        if user is None:
            user = fb_auth.create_user(
                email=email,
                password=password,
                display_name=display_name,
                email_verified=True,
            )
        else:
            # Reset password + display name every run so demo creds stay pinned.
            fb_auth.update_user(
                user.uid,
                password=password,
                display_name=display_name,
                disabled=False,
            )

        uid = user.uid
        claims = dict(user.custom_claims or {})
        claims["role"] = role_str
        fb_auth.set_custom_user_claims(uid, claims)

        # users/{uid} mirror doc (client-unwritable; role mirrors the claim).
        # Re-seed must not rewind the audit trail: on an EXISTING doc preserve
        # createdAt/createdBy and BUMP revision instead of resetting them
        # (specs/04 §4.12 — revision is a monotonic per-doc audit counter).
        mirror_ref = client.collection("users").document(uid)
        snapshot = mirror_ref.get()
        existing = snapshot.to_dict() if getattr(snapshot, "exists", False) else None

        mirror = {
            "uid": uid,
            "email": email,
            "displayName": display_name,
            "role": role_str,
            "status": "ACTIVE",
            "updatedAt": server_ts,
            "updatedBy": ACTOR_ID,
            "schemaVersion": 1,
        }
        if existing:
            mirror["createdAt"] = existing.get("createdAt", server_ts)
            mirror["createdBy"] = existing.get("createdBy", ACTOR_ID)
            mirror["revision"] = int(existing.get("revision", 0)) + 1
        else:
            mirror["createdAt"] = server_ts
            mirror["createdBy"] = ACTOR_ID
            mirror["revision"] = 0
        mirror_ref.set(mirror)
        provisioned.append(f"{email} -> {role_str}")

    return provisioned


def _server_ts():
    from google.cloud import firestore  # lazy — offline py_compile friendly

    return firestore.SERVER_TIMESTAMP
