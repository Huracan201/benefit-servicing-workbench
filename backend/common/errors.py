"""Typed domain exceptions for the safety-critical core.

Pure stdlib — this module must never import django or google.cloud so that the
framework-free core (money, periods, ids, state_machines, invariants) can be
unit-tested with zero third-party dependencies.
"""


class DomainError(Exception):
    """Base class for all domain-level errors raised by the core."""


class InvalidTransition(DomainError):
    """Raised when a state-machine transition (from -> to) is not allowed.

    See specs/06. The command layer maps this to HTTP 409 INVALID_TRANSITION.
    """

    def __init__(self, machine: str, frm: str, to: str):
        self.machine = machine
        self.frm = frm
        self.to = to
        super().__init__(
            f"invalid {machine} transition: {frm!r} -> {to!r}"
        )


class InvariantViolation(DomainError):
    """Raised when a financial invariant (I1-I7, specs/07 §7.2) would be violated.

    The command layer maps this to HTTP 409 INVARIANT_VIOLATION.
    """

    def __init__(self, invariant: str, message: str):
        self.invariant = invariant
        self.message = message
        super().__init__(f"{invariant}: {message}")
