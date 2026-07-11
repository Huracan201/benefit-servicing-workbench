"""Role constants and DRF permission classes for the write path.

Roles are a strict hierarchy for servicing actions (specs/README, specs/12
§12.2): ``OPERATIONS_USER < SERVICING_MANAGER < ADMINISTRATOR``. Every command
handler independently checks the caller's role — derived from the **verified
token claims**, never a Firestore lookup — against the capability matrix
(specs/12 §12.5). These permission classes provide that check declaratively.
"""

from __future__ import annotations

from rest_framework import permissions

# --- Role constants (Firebase custom-claim string values) ---------------
OPERATIONS_USER = "OPERATIONS_USER"
SERVICING_MANAGER = "SERVICING_MANAGER"
ADMINISTRATOR = "ADMINISTRATOR"

#: All valid roles, low -> high privilege.
ROLE_ORDER = (OPERATIONS_USER, SERVICING_MANAGER, ADMINISTRATOR)

#: Rank lookup for hierarchy comparisons (higher == more privileged).
ROLE_RANK = {role: rank for rank, role in enumerate(ROLE_ORDER)}


def role_rank(role) -> int:
    """Return the privilege rank of ``role``, or ``-1`` if unknown/absent.

    An unknown or missing role ranks below every real role, so it never
    satisfies a minimum-role check (specs/12 §12.3: no role claim == no access).
    """
    return ROLE_RANK.get(role, -1)


def role_satisfies(actual, minimum) -> bool:
    """True iff ``actual`` role meets or exceeds ``minimum`` in the hierarchy."""
    return role_rank(actual) >= role_rank(minimum)


class RequireRole(permissions.BasePermission):
    """Permission requiring at least ``min_role`` in the hierarchy.

    Usage (as a factory, since DRF instantiates permission classes itself)::

        permission_classes = [RequireRole.at_least(SERVICING_MANAGER)]
    """

    #: Subclasses / factory-built classes set this to the required minimum role.
    min_role = OPERATIONS_USER

    message = "You do not have the required role for this action."

    @classmethod
    def at_least(cls, min_role: str):
        """Build a concrete permission class requiring ``min_role``."""
        if min_role not in ROLE_RANK:
            raise ValueError(f"Unknown role: {min_role!r}")
        return type(
            f"RequireRole_{min_role}",
            (cls,),
            {"min_role": min_role},
        )

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        return role_satisfies(getattr(user, "role", None), self.min_role)


# --- Convenience permission classes -------------------------------------
RequireOperations = RequireRole.at_least(OPERATIONS_USER)
RequireManager = RequireRole.at_least(SERVICING_MANAGER)
RequireAdmin = RequireRole.at_least(ADMINISTRATOR)
