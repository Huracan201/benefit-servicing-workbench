"""Domain command layer (specs/19 §19.2, Phase 2).

This package holds the cross-cutting command machinery: the idempotency-aware
request hashing, the ``CommandError`` -> HTTP mapping, the ``CommandContext``
that carries actor/correlation/idempotency identity through a handler, and the
``transactional`` wrapper over Firestore's ``@firestore.transactional``.

The individual domain commands (process contribution, activate benefit, ...)
live in sibling modules and are built on top of :mod:`commands.base` and
:mod:`idempotency.service`.
"""
