"""Contribution-lifecycle command app (specs/09, specs/06 §6.1).

Owns the reconciliation sweeper entrypoint (:mod:`contributions.reconcile`) —
the crash-recovery half of the two-phase payment contract (specs/08 §8.4). The
forward process/retry commands live in :mod:`payments.service`; the finalize
transactions are shared between the two so recovery drives identical, guarded,
idempotent Phase-3 logic.
"""
