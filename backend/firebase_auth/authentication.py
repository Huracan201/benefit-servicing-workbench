"""DRF authentication backend that verifies Firebase ID tokens.

Every ``/api/v1`` request carries ``Authorization: Bearer <firebase-id-token>``.
Django verifies the token with the Firebase Admin SDK on every request (specs/12
§12.1). Per specs/12 §12.3 the verification uses ``check_revoked=True`` on every
**mutating** command (unsafe HTTP methods) so a disabled/offboarded user is
rejected immediately on the write path, and plain verification on safe methods
(reads/health) to avoid the extra Auth round-trip.

The authenticated principal is a lightweight object carrying the Firebase ``uid``
and the ``role`` custom claim (the authoritative role source — never a Firestore
lookup). Authorization against the capability matrix is done by the DRF
permission classes in :mod:`firebase_auth.permissions`.
"""

from __future__ import annotations

from rest_framework import authentication, exceptions

# HTTP methods that do not mutate state (RFC 7231). Reads use plain verification.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class FirebasePrincipal:
    """Minimal authenticated user standing in for ``request.user``.

    Not a Django ORM user (there is no ORM in this system). It implements the
    small surface DRF / permissions rely on: ``is_authenticated`` and identity.
    """

    def __init__(self, uid: str, claims: dict | None = None):
        self.uid = uid
        self.claims = claims or {}

    # --- DRF / Django auth surface -------------------------------------
    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def role(self):
        return self.claims.get("role")

    @property
    def email(self):
        return self.claims.get("email")

    @property
    def pk(self):
        return self.uid

    def __str__(self) -> str:  # for logging / audit
        return f"FirebasePrincipal(uid={self.uid!r}, role={self.role!r})"


class FirebaseAuthentication(authentication.BaseAuthentication):
    """Verify the Firebase ID token from the ``Authorization`` header."""

    keyword = "Bearer"

    def authenticate(self, request):
        auth_header = authentication.get_authorization_header(request).split()
        if not auth_header:
            return None  # no credentials -> let other authenticators / anon run

        if auth_header[0].decode("latin-1").lower() != self.keyword.lower():
            return None  # not a Bearer scheme -> not ours

        if len(auth_header) == 1:
            raise exceptions.AuthenticationFailed(
                "Invalid Authorization header: no token provided."
            )
        if len(auth_header) > 2:
            raise exceptions.AuthenticationFailed(
                "Invalid Authorization header: token contains spaces."
            )

        token = auth_header[1].decode("latin-1")
        check_revoked = request.method not in SAFE_METHODS
        claims = self._verify(token, check_revoked=check_revoked)

        uid = claims.get("uid") or claims.get("sub")
        if not uid:
            raise exceptions.AuthenticationFailed("Token missing subject (uid).")

        principal = FirebasePrincipal(uid=uid, claims=claims)
        return (principal, token)

    def authenticate_header(self, request):
        # Drives the WWW-Authenticate header on a 401.
        return self.keyword

    # -----------------------------------------------------------------
    def _verify(self, token: str, *, check_revoked: bool) -> dict:
        """Verify the ID token via the Admin SDK; raise on any failure.

        Imports are lazy so this module can be ``py_compile``d offline.
        """
        from firebase_admin import auth as firebase_auth_sdk

        from . import admin_init

        admin_init.initialize_app()

        try:
            return firebase_auth_sdk.verify_id_token(
                token, check_revoked=check_revoked
            )
        except firebase_auth_sdk.RevokedIdTokenError:
            raise exceptions.AuthenticationFailed("Token has been revoked.")
        except firebase_auth_sdk.UserDisabledError:
            raise exceptions.AuthenticationFailed("User account is disabled.")
        except firebase_auth_sdk.ExpiredIdTokenError:
            raise exceptions.AuthenticationFailed("Token has expired.")
        except firebase_auth_sdk.InvalidIdTokenError:
            raise exceptions.AuthenticationFailed("Invalid authentication token.")
        except Exception:  # noqa: BLE001 — any verification error is a 401
            raise exceptions.AuthenticationFailed("Could not verify token.")
