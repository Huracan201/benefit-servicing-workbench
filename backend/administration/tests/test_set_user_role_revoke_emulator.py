"""Emulator integration tests for refresh-token revocation on role demotion.

Drives :func:`administration.services.set_user_role` against the **Auth** emulator
(alongside Firestore): a *demotion* must call ``revoke_refresh_tokens`` so a token
minted before the change is rejected by the write-path
``verify_id_token(check_revoked=True)`` (specs/12 §12.3) immediately, rather than
lingering for the ~1h ID-token TTL. The revoke lives INSIDE the NEW-outcome
idempotency gate, so an ``Idempotency-Key`` replay must NOT re-revoke; a
*promotion* must not revoke at all.

Requires the Auth emulator in addition to Firestore, so it also gates on
``FIREBASE_AUTH_EMULATOR_HOST`` (both are exported by ``firebase emulators:exec``).
Refresh-token revocation is **second-resolution** (Firebase stores ``validSince``
in whole seconds), so the test sleeps ~1s at the points where a later-second
``validSince`` must be observable — this is the documented way to make revocation
deterministic, not incidental slack.
"""

from __future__ import annotations

import json
import os
import time
import unittest
import urllib.error
import urllib.request
import uuid

from django.test import SimpleTestCase, tag

from administration.services import set_user_role
from commands.base import CommandContext, request_hash
from common.firestore import get_client
from firebase_auth.permissions import (
    ADMINISTRATOR,
    OPERATIONS_USER,
    SERVICING_MANAGER,
)

EMULATOR = bool(os.environ.get("FIRESTORE_EMULATOR_HOST")) and bool(
    os.environ.get("FIREBASE_AUTH_EMULATOR_HOST")
)

PASSWORD = "DemoPass!234"


def _create_user_with_role(email: str, role: str) -> str:
    """Create a fresh Auth-emulator user carrying ``role`` as its custom claim."""
    from firebase_admin import auth as fb_auth

    from firebase_auth import admin_init

    admin_init.initialize_app()
    user = fb_auth.create_user(email=email, password=PASSWORD, email_verified=True)
    fb_auth.set_custom_user_claims(user.uid, {"role": role})
    return user.uid


def _mint_id_token(email: str) -> str:
    """Sign in via the Auth-emulator REST API and return a live ID token.

    The emulator signs tokens for its single configured project (singleProjectMode),
    so the returned token verifies against the same project the Admin SDK uses.
    """
    host = os.environ["FIREBASE_AUTH_EMULATOR_HOST"]
    url = (
        f"http://{host}/identitytoolkit.googleapis.com/v1/"
        "accounts:signInWithPassword?key=fake-api-key"
    )
    payload = json.dumps(
        {"email": email, "password": PASSWORD, "returnSecureToken": True}
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:  # pragma: no cover - diagnostic only
        detail = exc.read().decode("utf-8", "replace")
        raise AssertionError(f"emulator sign-in failed ({exc.code}): {detail}") from exc
    return body["idToken"]


def _ctx(uid: str, role: str) -> CommandContext:
    """An ADMINISTRATOR command context with a fresh unique idempotency key."""
    return CommandContext(
        actor_id="user_test_admin",
        actor_role=ADMINISTRATOR,
        actor_name="Test Administrator",
        idempotency_key=f"role_{uuid.uuid4().hex}",
        request_hash=request_hash("POST", f"/admin/users/{uid}/role", {"role": role}),
    )


@tag("emulator")
@unittest.skipUnless(
    EMULATOR, "requires FIRESTORE_EMULATOR_HOST and FIREBASE_AUTH_EMULATOR_HOST"
)
class SetUserRoleRevokeTests(SimpleTestCase):
    databases: list[str] = []

    def test_demotion_revokes_tokens_and_replay_does_not(self):
        from firebase_admin import auth as fb_auth

        from firebase_auth import admin_init

        admin_init.initialize_app()
        client = get_client()

        email = f"revoke_{uuid.uuid4().hex[:10]}@demo.test"
        uid = _create_user_with_role(email, SERVICING_MANAGER)

        # A live ID token minted BEFORE the demotion (embeds the MANAGER claim). It
        # verifies cleanly on the write path while the user still holds the role.
        token = _mint_id_token(email)
        fb_auth.verify_id_token(token, check_revoked=True)  # not revoked yet

        before = fb_auth.get_user(uid).tokens_valid_after_timestamp or 0

        # validSince is second-resolution: make sure the revoke lands in a strictly
        # later second than the token's iat so the revocation is observable.
        time.sleep(1.1)

        ctx = _ctx(uid, OPERATIONS_USER)
        result = set_user_role(
            uid=uid, role=OPERATIONS_USER, ctx=ctx, client=client
        )
        self.assertEqual(result["role"], OPERATIONS_USER)
        self.assertEqual(result["previousRole"], SERVICING_MANAGER)

        # tokens_valid_after advanced — the demotion revoked the refresh tokens.
        after = fb_auth.get_user(uid).tokens_valid_after_timestamp or 0
        self.assertGreater(after, before)

        # The pre-change token is now rejected on the mutating (write) path.
        with self.assertRaises(fb_auth.RevokedIdTokenError):
            fb_auth.verify_id_token(token, check_revoked=True)

        # Wait past another second so a (buggy) re-revoke would move validSince to a
        # later second, then replay the SAME Idempotency-Key: it must return the
        # STORED result (previousRole still MANAGER — a re-run would see OPERATIONS,
        # the now-current claim) and must NOT re-revoke.
        time.sleep(1.1)
        replay = set_user_role(
            uid=uid, role=OPERATIONS_USER, ctx=ctx, client=client
        )
        self.assertEqual(replay["previousRole"], SERVICING_MANAGER)
        self.assertEqual(
            fb_auth.get_user(uid).tokens_valid_after_timestamp or 0, after
        )

    def test_promotion_does_not_revoke_tokens(self):
        from firebase_admin import auth as fb_auth

        from firebase_auth import admin_init

        admin_init.initialize_app()
        client = get_client()

        email = f"promote_{uuid.uuid4().hex[:10]}@demo.test"
        uid = _create_user_with_role(email, OPERATIONS_USER)

        token = _mint_id_token(email)
        before = fb_auth.get_user(uid).tokens_valid_after_timestamp or 0

        # Sleep so a (buggy) revoke would land in a later second and be detectable.
        time.sleep(1.1)

        result = set_user_role(
            uid=uid,
            role=SERVICING_MANAGER,
            ctx=_ctx(uid, SERVICING_MANAGER),
            client=client,
        )
        self.assertEqual(result["role"], SERVICING_MANAGER)
        self.assertEqual(result["previousRole"], OPERATIONS_USER)

        # No revocation on a promotion: validSince is unchanged and the pre-change
        # token still verifies on the write path.
        self.assertEqual(
            fb_auth.get_user(uid).tokens_valid_after_timestamp or 0, before
        )
        fb_auth.verify_id_token(token, check_revoked=True)  # still valid
