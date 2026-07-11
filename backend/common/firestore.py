"""Firestore client seam (specs/README seams; specs/08 §8.1 txn constraints).

get_client() -> a google.cloud.firestore client, emulator-aware via
FIRESTORE_EMULATOR_HOST, as a lazy singleton. The google.cloud.firestore import
is done INSIDE the function so this module imports cleanly even when the package
is absent — the pure core (money/periods/ids/state_machines/invariants/enums/
errors) must never pull google.cloud in at import time.

Keep this module import-light: do NOT import the pure core here at module scope.
"""

import os
import threading

_client = None
_lock = threading.Lock()


def get_client():
    """Return a process-wide singleton google.cloud.firestore client.

    Emulator-aware: when FIRESTORE_EMULATOR_HOST is set the underlying client
    library targets the emulator automatically. GOOGLE_CLOUD_PROJECT /
    BSW_FIRESTORE_PROJECT selects the project id (a placeholder is fine against
    the emulator).
    """
    global _client
    if _client is not None:
        return _client
    with _lock:
        if _client is None:
            # Lazy import: the package need not be installed to import this module.
            from google.cloud import firestore  # noqa: WPS433 (deliberate lazy import)

            project = (
                os.environ.get("GOOGLE_CLOUD_PROJECT")
                or os.environ.get("BSW_FIRESTORE_PROJECT")
                or os.environ.get("GCLOUD_PROJECT")
            )
            if project:
                _client = firestore.Client(project=project)
            else:
                _client = firestore.Client()
    return _client


def reset_client() -> None:
    """Drop the cached client (test hook)."""
    global _client
    with _lock:
        _client = None


def run_transaction(update_fn, *args, **kwargs):
    """Thin helper: run `update_fn(transaction, *args, **kwargs)` transactionally.

    Firestore transactions require all reads before all writes and auto-retry on
    contention (specs/08 §8.1). `update_fn` must be decorated appropriately or
    accept the transaction as its first argument; this wrapper obtains a
    transaction from the singleton client and drives it.
    """
    client = get_client()
    from google.cloud import firestore  # lazy — see module docstring.

    transaction = client.transaction()

    @firestore.transactional
    def _txn(txn):
        return update_fn(txn, *args, **kwargs)

    return _txn(transaction)
