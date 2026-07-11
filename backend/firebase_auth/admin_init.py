"""Idempotent initialization of the Firebase Admin SDK.

The backend authenticates using Application Default Credentials (ADC) — there
are **no JSON key files anywhere** (specs/21 §runtime). On Cloud Run ADC is the
attached service account; in local/emulator dev the Auth emulator is used and no
real credentials are required.

Emulator awareness is automatic: the Firebase Admin SDK and the underlying
``google.auth`` libraries honour ``FIREBASE_AUTH_EMULATOR_HOST`` /
``FIRESTORE_EMULATOR_HOST`` from the environment, so this module does not need to
special-case them beyond selecting anonymous credentials when the Auth emulator
is active.

Imports of ``firebase_admin`` are performed lazily inside the initializer so this
module can be imported (and ``py_compile``d) in an offline sandbox where the
package is not installed.
"""

from __future__ import annotations

import os
import threading

_INIT_LOCK = threading.Lock()
_APP = None


def _project_id() -> str | None:
    return (
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        or os.environ.get("GCLOUD_PROJECT")
        or os.environ.get("FIREBASE_PROJECT_ID")
    )


def initialize_app():
    """Initialize (once) and return the default Firebase Admin app.

    Idempotent: safe to call from many code paths and repeatedly. If the default
    app already exists (e.g. initialized elsewhere) it is returned as-is.
    """
    global _APP
    if _APP is not None:
        return _APP

    with _INIT_LOCK:
        if _APP is not None:
            return _APP

        import firebase_admin  # lazy import — offline py_compile friendly

        # Reuse an already-initialized default app if present.
        try:
            _APP = firebase_admin.get_app()
            return _APP
        except ValueError:
            pass

        options = {}
        project_id = _project_id()
        if project_id:
            options["projectId"] = project_id

        if os.environ.get("FIREBASE_AUTH_EMULATOR_HOST"):
            # Auth emulator: no real credentials. firebase_admin.credentials has no
            # AnonymousCredentials, so wrap google.auth's in a credentials.Base subclass.
            from firebase_admin import credentials
            from google.auth.credentials import AnonymousCredentials

            class _EmulatorCredential(credentials.Base):
                def get_credential(self):
                    return AnonymousCredentials()

            _APP = firebase_admin.initialize_app(_EmulatorCredential(), options or None)
        else:
            # Production/Cloud Run: Application Default Credentials.
            _APP = firebase_admin.initialize_app(options=options or None)

        return _APP
