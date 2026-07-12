"""``internal`` — the async infrastructure foundation (specs/14, specs/21 §21.5).

This app owns the shared scaffolding every deferred/scheduled handler builds on:

* :mod:`internal.enqueue` — the ``enqueue(task, payload, ctx)`` seam keyed on
  ``settings.TASK_EXECUTION_MODE`` (``inline`` runs the same callable the cloud
  ``/internal/tasks/<task>`` view invokes; ``cloud`` mints an OIDC Cloud Task),
  plus the task/job registries and ``QUEUE_CONFIG``.
* :mod:`internal.system_context` — ``system_ctx(job)``, the un-forgeable SYSTEM
  actor context carried through async work (specs/12 §12.5).
* :mod:`internal.dead_letter` — the retryable/terminal → HTTP envelope and
  final-attempt ``TASK_FAILED`` dead-lettering (specs/14 §14.5).
* :mod:`internal.views` — the base ``/internal/tasks/*`` + ``/internal/jobs/*``
  handlers, protected at ingress by the already-fail-closed
  ``firebase_auth.middleware.InternalOIDCMiddleware`` (specs/12 §12.5).

It declares no ORM models — Firestore is the only datastore. Third-party
imports (``google.cloud.tasks``) are lazy so every module ``py_compile``s in an
offline sandbox where the client libraries are absent.
"""
