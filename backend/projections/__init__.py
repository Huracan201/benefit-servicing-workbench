"""``projections`` — the read-model projection layer (specs/05).

This app owns the source-derivation engine behind the eventually-consistent read
models:

* :mod:`projections.recompute` — the pure ``recompute_*`` functions plus
  :func:`~projections.recompute.apply_key`, shared by **both** the event-driven
  ``update-projection`` task and the scheduled ``rebuild-summaries`` job (they call
  the *same* functions, so event-driven and scheduled outputs agree by
  construction), and the projection ``Key`` kind constants that flow through the
  task payload naming which summary docs to recompute.

The engine *reads source, never a projection*: a recompute rederives the whole doc
from ``benefitAgreements`` / ``scheduledContributions`` / ``loans`` with bounded
queries and never folds an event delta — recompute-from-source is what makes an
at-least-once redelivery converge (a redelivered increment double-counts; a
redelivered recompute is byte-identical). A projection is never read to make a
financial decision (specs/05 §5.1).

It declares no ORM models — Firestore is the only datastore. Third-party imports
(``google.cloud.firestore``) are lazy so every module ``py_compile``s in an offline
sandbox where the client libraries are absent.
"""
