"""Idempotency record lifecycle (specs/08 §8.2-§8.3, specs/04 §4.11).

The single authoritative "did this operation start" mechanism: the idempotency
record is created **inside** the state-transition transaction with a
create-precondition, so the first same-key request wins atomically and any
concurrent or replayed request observes the in-progress/completed fact instead
of double-executing. See :mod:`idempotency.service`.
"""
