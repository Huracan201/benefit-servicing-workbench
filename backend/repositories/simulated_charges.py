"""Payment-simulator ledger data-access gateway —
``simulatedCharges/{processorIdempotencyKey}`` (specs/04 §4.1, specs/09 §9.5).

Client-invisible. Keyed by the deterministic ``processorIdempotencyKey``
(``common.ids.processor_key``). The simulated payment adapter reads/writes here
to make ``charge`` idempotent and to fence UNKNOWN keys with a NOT_SUBMITTED
tombstone (specs/08 §8.4).
"""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, processor_key: str):
    """``DocumentReference`` for ``simulatedCharges/{processor_key}``."""
    return refs.doc(client, refs.SIMULATED_CHARGES, processor_key)


def get(client, processor_key: str) -> Optional[dict[str, Any]]:
    """Read the simulated-charge ledger entry as dict-with-id, or ``None``."""
    return refs.get(client, refs.SIMULATED_CHARGES, processor_key)
