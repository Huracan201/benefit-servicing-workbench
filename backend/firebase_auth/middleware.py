"""Ingress auth for internal task/scheduler endpoints (``/internal/*``).

The Cloud Run service is deployed ``--allow-unauthenticated`` (Firebase ID tokens
are app-layer, not Google IAM), so ``/internal/tasks/*`` and ``/internal/jobs/*``
handler URLs are internet-reachable and MUST verify their caller (specs/12
§12.5):

- **Cloud (default):** verify the Google-signed OIDC JWT minted by Cloud Tasks /
  Scheduler for the dedicated invoker service account, asserting
  ``aud == TASKS_AUDIENCE`` **and** ``email == TASKS_INVOKER_SA``. Firebase user
  tokens are never accepted on ``/internal/*``.
- **Local/emulator:** when ``FIRESTORE_EMULATOR_HOST`` is set, accept a shared
  secret header ``X-Internal-Auth == INTERNAL_DEV_SECRET`` instead, preserving the
  direct-invocation dev loop.

Anything else on ``/internal/*`` → ``403``. Non-``/internal`` paths pass through
untouched. ``google.*`` imports are lazy so this module ``py_compile``s offline.
"""

from __future__ import annotations

import hmac
import os

from django.http import JsonResponse

INTERNAL_PREFIX = "/internal/"


class InternalOIDCMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path
        if path == "/internal" or path.startswith(INTERNAL_PREFIX):
            denial = self._authorize(request)
            if denial is not None:
                return denial
        return self.get_response(request)

    # -----------------------------------------------------------------
    def _authorize(self, request):
        """Return a 403 response if the caller is not authorized, else ``None``."""
        # Dev/emulator: shared-secret header bypass.
        if os.environ.get("FIRESTORE_EMULATOR_HOST"):
            expected = os.environ.get("INTERNAL_DEV_SECRET", "")
            provided = request.META.get("HTTP_X_INTERNAL_AUTH", "")
            if expected and provided and hmac.compare_digest(provided, expected):
                return None
            return self._forbidden("Invalid internal dev secret.")

        # Cloud: verify Google OIDC token for the invoker service account.
        auth_header = request.META.get("HTTP_AUTHORIZATION", "")
        parts = auth_header.split()
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return self._forbidden("Missing OIDC bearer token.")
        token = parts[1]

        audience = os.environ.get("TASKS_AUDIENCE")
        invoker_sa = os.environ.get("TASKS_INVOKER_SA")
        if not audience or not invoker_sa:
            # Misconfigured deployment: fail closed.
            return self._forbidden("Internal ingress not configured.")

        try:
            from google.auth.transport import requests as google_requests
            from google.oauth2 import id_token as google_id_token

            claims = google_id_token.verify_oauth2_token(
                token, google_requests.Request(), audience=audience
            )
        except Exception:  # noqa: BLE001 — any verification failure is a 403
            return self._forbidden("OIDC token verification failed.")

        if claims.get("email") != invoker_sa:
            return self._forbidden("OIDC caller is not the invoker service account.")
        if not claims.get("email_verified", False):
            return self._forbidden("OIDC caller email not verified.")

        return None

    @staticmethod
    def _forbidden(detail: str):
        return JsonResponse({"detail": detail}, status=403)
