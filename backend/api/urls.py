"""``/api/v1`` URL map — Phase-2 business commands (specs/11 §11.4).

Mounted by ``config.urls`` at the ``api/v1/`` prefix. All specs/19 §19.2 domain
commands are wired here:

* ``POST /benefit-agreements/{agreementId}/activate|suspend|resume|terminate`` — MANAGER+ (specs/10 §10.1–10.3)
* ``POST /borrowers/{borrowerId}/employment-status``  — MANAGER+  (specs/10 §10.4)
* ``POST /contributions/{contributionId}/process``    — MANAGER+  (specs/09 §9.1)
* ``POST /contributions/{contributionId}/retry``      — OPERATIONS+ (specs/09)
* ``POST /exceptions`` + ``{id}/assign|mark-in-review|resolve|dismiss`` — OPERATIONS+ (specs/09 §9.3, specs/06 §6.4)
* ``POST /loans/{loanId}/notes``                      — OPERATIONS+ (specs/10 §10.5)
* ``POST /admin/users/{uid}/role`` + ``/admin/employers/{employerId}/status`` — ADMIN (specs/12)

Path parameter names are chosen to match each view's handler signature: the
benefit/employment/notes/exception/admin views take their id positionally; the
payment views read ``contributionId`` (or the snake-case alias) from ``kwargs``.
"""

from __future__ import annotations

from django.urls import path

from administration.views import SetEmployerStatusView, SetUserRoleView
from benefits.views import (
    ActivateBenefitView,
    ResumeBenefitView,
    SuspendBenefitView,
    TerminateBenefitView,
)
from employment.views import EmploymentStatusView
from exceptions.views import (
    AssignExceptionView,
    CreateExceptionView,
    DismissExceptionView,
    MarkInReviewView,
    ResolveExceptionView,
)
from notes.views import AddNoteView
from payments.views import ProcessContributionView, RetryContributionView

app_name = "api"

urlpatterns = [
    # --- Benefit agreements (specs/11 §11.4, specs/10 §10.1–10.3) ---
    path(
        "benefit-agreements/<str:agreement_id>/activate",
        ActivateBenefitView.as_view(),
        name="benefit-activate",
    ),
    path(
        "benefit-agreements/<str:agreement_id>/suspend",
        SuspendBenefitView.as_view(),
        name="benefit-suspend",
    ),
    path(
        "benefit-agreements/<str:agreement_id>/resume",
        ResumeBenefitView.as_view(),
        name="benefit-resume",
    ),
    path(
        "benefit-agreements/<str:agreement_id>/terminate",
        TerminateBenefitView.as_view(),
        name="benefit-terminate",
    ),

    # --- Borrower employment (specs/11 §11.4, specs/10 §10.4) ---
    path(
        "borrowers/<str:borrower_id>/employment-status",
        EmploymentStatusView.as_view(),
        name="borrower-employment-status",
    ),

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

    # --- Exceptions (specs/11 §11.4, specs/09 §9.3, specs/06 §6.4) ---
    path(
        "exceptions",
        CreateExceptionView.as_view(),
        name="exception-create",
    ),
    path(
        "exceptions/<str:exception_id>/assign",
        AssignExceptionView.as_view(),
        name="exception-assign",
    ),
    path(
        "exceptions/<str:exception_id>/mark-in-review",
        MarkInReviewView.as_view(),
        name="exception-mark-in-review",
    ),
    path(
        "exceptions/<str:exception_id>/resolve",
        ResolveExceptionView.as_view(),
        name="exception-resolve",
    ),
    path(
        "exceptions/<str:exception_id>/dismiss",
        DismissExceptionView.as_view(),
        name="exception-dismiss",
    ),

    # --- Notes (specs/11 §11.4, specs/10 §10.5) ---
    path(
        "loans/<str:loan_id>/notes",
        AddNoteView.as_view(),
        name="loan-note-add",
    ),

    # --- Admin (specs/11 §11.4, specs/12) ---
    path(
        "admin/users/<str:uid>/role",
        SetUserRoleView.as_view(),
        name="admin-user-role",
    ),
    path(
        "admin/employers/<str:employer_id>/status",
        SetEmployerStatusView.as_view(),
        name="admin-employer-status",
    ),
]
