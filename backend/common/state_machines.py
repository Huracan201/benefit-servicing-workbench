"""State machines (specs/06). Implemented once, used by every command/task.

The API never accepts a raw target status; the command layer computes a
transition and validates it here inside the Firestore transaction (with a
read-the-status precondition — specs/06 §6.7). Every transition not listed is
invalid and raises InvalidTransition.

Pure stdlib + common.errors — no django, no google.cloud.
"""

from typing import Dict, FrozenSet, Tuple

from common.errors import InvalidTransition

Transition = Tuple[str, str]


# --- Allowed transition sets (specs/06). Each is a frozenset of (from, to). ---

CONTRIBUTION_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("SCHEDULED", "PROCESSING"),
    ("SCHEDULED", "CANCELED"),
    ("PROCESSING", "POSTED"),
    ("PROCESSING", "FAILED"),
    ("PROCESSING", "CANCELED"),
    ("FAILED", "RETRY_PENDING"),
    ("FAILED", "CANCELED"),
    ("RETRY_PENDING", "PROCESSING"),
    ("RETRY_PENDING", "CANCELED"),
})

ATTEMPT_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("STARTED", "SUCCEEDED"),
    ("STARTED", "FAILED"),
})

BENEFIT_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("DRAFT", "PENDING"),
    ("PENDING", "ACTIVATING"),
    ("ACTIVATING", "ACTIVE"),
    ("ACTIVATING", "PENDING"),
    ("ACTIVATING", "TERMINATED"),
    ("ACTIVE", "SUSPENDED"),
    ("ACTIVE", "TERMINATED"),
    ("SUSPENDED", "ACTIVE"),
    ("SUSPENDED", "TERMINATED"),
    ("ACTIVE", "COMPLETED"),
    ("SUSPENDED", "COMPLETED"),
})

EXCEPTION_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("OPEN", "IN_REVIEW"),
    ("OPEN", "RESOLVED"),
    ("OPEN", "DISMISSED"),
    ("IN_REVIEW", "RESOLVED"),
    ("IN_REVIEW", "DISMISSED"),
})

EMPLOYMENT_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("PENDING", "ACTIVE"),
    ("ACTIVE", "LEAVE"),
    ("LEAVE", "ACTIVE"),
    ("ACTIVE", "TERMINATED"),
    ("LEAVE", "TERMINATED"),
})

LOAN_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("ACTIVE", "PAID_OFF"),
    ("ACTIVE", "DELINQUENT"),
    ("DELINQUENT", "ACTIVE"),
    ("ACTIVE", "CLOSED"),
    ("PAID_OFF", "CLOSED"),
})

EMPLOYER_TRANSITIONS: FrozenSet[Transition] = frozenset({
    ("ACTIVE", "INACTIVE"),
    ("INACTIVE", "ACTIVE"),
})


class StateMachine:
    """A named set of allowed (from -> to) transitions."""

    def __init__(self, name: str, transitions: FrozenSet[Transition]):
        self.name = name
        self.transitions = transitions

    def can_transition(self, frm, to) -> bool:
        """True if (frm -> to) is an allowed transition."""
        return (_val(frm), _val(to)) in self.transitions

    def assert_transition(self, frm, to) -> None:
        """Raise InvalidTransition unless (frm -> to) is allowed."""
        if not self.can_transition(frm, to):
            raise InvalidTransition(self.name, _val(frm), _val(to))


def _val(x) -> str:
    """Accept either an enum member (with .value) or a raw string."""
    return getattr(x, "value", x)


MACHINES: Dict[str, StateMachine] = {
    "contribution": StateMachine("contribution", CONTRIBUTION_TRANSITIONS),
    "attempt": StateMachine("attempt", ATTEMPT_TRANSITIONS),
    "benefit": StateMachine("benefit", BENEFIT_TRANSITIONS),
    "exception": StateMachine("exception", EXCEPTION_TRANSITIONS),
    "employment": StateMachine("employment", EMPLOYMENT_TRANSITIONS),
    "loan": StateMachine("loan", LOAN_TRANSITIONS),
    "employer": StateMachine("employer", EMPLOYER_TRANSITIONS),
}


def get_machine(name: str) -> StateMachine:
    try:
        return MACHINES[name]
    except KeyError:
        raise KeyError(f"unknown state machine {name!r}")


def can_transition(name: str, frm, to) -> bool:
    """Module-level convenience: is (frm -> to) allowed on machine `name`?"""
    return get_machine(name).can_transition(frm, to)


def assert_transition(name: str, frm, to) -> None:
    """Module-level convenience: raise InvalidTransition unless allowed."""
    get_machine(name).assert_transition(frm, to)
