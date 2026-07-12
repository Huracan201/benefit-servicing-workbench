"""``system_ctx`` — the SYSTEM actor context for async work (specs/12 §12.5).

Cloud Tasks / Scheduler handlers run with no authenticated human: they act as
SYSTEM. This module mints the one :class:`~commands.base.CommandContext` that
carries the **un-forgeable** ``is_system`` marker (set nowhere else), so
command-level role guards (U2) can authorize SYSTEM-driven writes without a
Firebase role, and every servicing event records ``createdBy = system:<job>``.

The correlation id is namespaced per run — ``sys:<job>:<uuid>`` — so async
events never collide with a foreground command on ``(correlationId, sequence)``
(specs/08 §8.5). This generalizes the ad-hoc ``_system_ctx`` in
:mod:`contributions.reconcile`, which later slices can migrate onto this seam.
"""

from __future__ import annotations

import uuid
from typing import Optional

from commands.base import CommandContext


def system_ctx(
    job: str, *, correlation_id: Optional[str] = None, verified: bool = True
) -> CommandContext:
    """Return a SYSTEM-actor context for ``job`` (specs/12 §12.5).

    * ``actor_id`` / ``createdBy`` — ``system:<job>`` (``createdBy`` is derived
      from ``actor_id`` by the ``repositories.stamp_*`` helpers).
    * ``actor_role`` — ``None``: SYSTEM has no Firebase role; ``is_system`` is the
      authorization signal instead.
    * ``is_system`` — ``verified`` (the marker set ONLY here). In-process callers
      (``internal.enqueue`` running a task inline) are inherently trusted and use
      the default ``True``; the ``/internal`` request handler passes the actual
      ``request.internal_verified`` marker, so a request that did NOT pass
      :class:`~firebase_auth.middleware.InternalOIDCMiddleware` yields a
      NON-SYSTEM context the authority guard rejects — never forgeable from input.
    * ``correlation_id`` — ``sys:<job>:<uuid>`` unless an explicit id is passed,
      keeping async events off any foreground command's correlation namespace.
    """
    return CommandContext(
        actor_id=f"system:{job}",
        actor_role=None,
        actor_name=f"System ({job})",
        correlation_id=correlation_id or f"sys:{job}:{uuid.uuid4().hex}",
        is_system=verified,
    )
