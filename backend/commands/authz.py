"""Command-boundary authority guard (specs/12 §12.5, defense-in-depth).

Ingress authorization is already enforced twice: Firestore rules gate reads, and
DRF ``RequireRole`` / :class:`~firebase_auth.middleware.InternalOIDCMiddleware`
gate writes at the HTTP edge. specs/12 §12.5 nonetheless requires that **every
command handler independently checks authority** — so a routing mistake or a
bypassed middleware can never let an under-privileged (or unauthenticated)
caller drive a write.

:func:`require_system_or_role` is that inner check. It authorizes on either of two
independent signals:

* the **un-forgeable SYSTEM marker** (``ctx.is_system``) minted ONLY by
  :func:`internal.system_context.system_ctx` for Cloud Tasks / Scheduler work —
  never derivable from request input (a request-built context is always
  non-SYSTEM); or
* the caller's Firebase-claim ``actor_role`` meeting ``min_role`` in the
  hierarchy (:func:`firebase_auth.permissions.role_satisfies`).

The SYSTEM case is special-cased **first** and deliberately: a SYSTEM context has
``actor_role=None`` (rank ``-1``), so a naive role-only check would false-reject
every legitimate async task.
"""

from __future__ import annotations

from commands.base import CommandError
from firebase_auth.permissions import ROLE_RANK, role_satisfies


class AuthorityDenied(CommandError):
    """Caller is neither SYSTEM nor role-authorized for this command (403).

    Command-boundary authority failure (specs/12 §12.5). At the ``/internal``
    boundary an authority denial is **terminal**: the envelope returns 2xx +
    ``TASK_FAILED`` rather than retrying, since re-delivery cannot grant authority.
    """

    http_status = 403
    code = "FORBIDDEN"


def require_system_or_role(ctx, *, min_role: str) -> None:
    """Assert ``ctx`` may drive a command, else raise :class:`AuthorityDenied`.

    Passes iff the context carries the verified-SYSTEM marker OR its
    ``actor_role`` satisfies ``min_role``. The SYSTEM marker is checked first so a
    SYSTEM context (``actor_role=None``, rank ``-1``) is never false-rejected by
    the role comparison (specs/12 §12.5).
    """
    if getattr(ctx, "is_system", False):
        return
    # Fail closed on a missing/unknown min_role (a programming error): otherwise
    # role_rank(min_role) == -1 would make every actor — even a role-less one —
    # satisfy the check and the guard would admit everyone.
    if min_role not in ROLE_RANK:
        raise AuthorityDenied(f"unknown required role {min_role!r}")
    if role_satisfies(getattr(ctx, "actor_role", None), min_role):
        return
    raise AuthorityDenied(
        f"actor role {getattr(ctx, 'actor_role', None)!r} is not authorized "
        f"(requires SYSTEM or >= {min_role})"
    )
