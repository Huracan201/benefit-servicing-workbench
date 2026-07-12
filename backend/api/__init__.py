"""api — the ``/api/v1`` business-command URL surface (specs/11 §11.4).

This package is a thin URLconf aggregator: it imports the DRF view seams that
the domain-command apps expose (``benefits.views``, ``payments.views``) and
mounts them at the specs/11 §11.4 paths under the ``/api/v1/`` prefix (wired in
``config.urls``).

It is intentionally NOT a Django app — it declares no models, ships no
``apps.py``, and is not listed in ``INSTALLED_APPS``. It is imported only as a
URLconf module via ``include("api.urls")``.

Phase 2 implements three commands (specs/19 §19.2): benefit activation and
contribution process/retry. The remaining specs/11 §11.4 endpoints
(suspend/resume/terminate, employment-status, exceptions, notes, admin) land in
later phases and are left commented in ``api.urls`` with a pointer to the spec.
"""
