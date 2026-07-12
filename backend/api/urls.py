"""``/api/v1`` URL map — Phase-2 business commands (specs/11 §11.4).

Mounted by ``config.urls`` at the ``api/v1/`` prefix. Only the three commands
implemented this phase (specs/19 §19.2) are wired:

* ``POST /benefit-agreements/{agreementId}/activate``  — MANAGER+  (specs/10 §10.1)
* ``POST /contributions/{contributionId}/process``     — MANAGER+  (specs/09 §9.1)
* ``POST /contributions/{contributionId}/retry``       — OPERATIONS+ (specs/09)

The remaining specs/11 §11.4 endpoints are listed but commented out — they are
delivered in later phases (suspend/resume/terminate, employment-status,
exceptions CRUD, loan notes, admin role/employer-status). Path parameter names
are chosen to match each view's handler signature: the benefit view takes
``agreement_id`` positionally; the payment views read ``contributionId`` (or the
snake-case alias) from ``kwargs``.
"""

from __future__ import annotations

from django.urls import path

from benefits.views import ActivateBenefitView
from payments.views import ProcessContributionView, RetryContributionView

app_name = "api"

urlpatterns = [
    # --- Benefit agreements (specs/11 §11.4, specs/10 §10.1) ---
    path(
        "benefit-agreements/<str:agreement_id>/activate",
        ActivateBenefitView.as_view(),
        name="benefit-activate",
    ),
    # path("benefit-agreements/<str:agreement_id>/suspend", ...),    # later phase (specs/10 §10.2)
    # path("benefit-agreements/<str:agreement_id>/resume", ...),     # later phase (specs/10 §10.2)
    # path("benefit-agreements/<str:agreement_id>/terminate", ...),  # later phase (specs/10 §10.3)

    # --- Borrower employment (specs/11 §11.4, specs/10 §10.4) ---
    # path("borrowers/<str:borrower_id>/employment-status", ...),    # later phase

    # --- Contributions (specs/11 §11.4, specs/09) ---
    path(
        "contributions/<str:contributionId>/process",
        ProcessContributionView.as_view(),
        name="contribution-process",
    ),
    path(
        "contributions/<str:contributionId>/retry",
        RetryContributionView.as_view(),
        name="contribution-retry",
    ),

    # --- Exceptions (specs/11 §11.4, specs/09 §9.3) ---
    # path("exceptions", ...),                                  # later phase
    # path("exceptions/<str:exception_id>/assign", ...),        # later phase
    # path("exceptions/<str:exception_id>/mark-in-review", ...),# later phase
    # path("exceptions/<str:exception_id>/resolve", ...),       # later phase
    # path("exceptions/<str:exception_id>/dismiss", ...),       # later phase

    # --- Notes (specs/11 §11.4) ---
    # path("loans/<str:loan_id>/notes", ...),                   # later phase

    # --- Admin (specs/11 §11.4, specs/12 §12) ---
    # path("admin/users/<str:uid>/role", ...),                  # later phase
    # path("admin/employers/<str:employer_id>/status", ...),    # later phase
]
