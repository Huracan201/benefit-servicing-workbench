"""Benefit-agreement data-access gateway — ``benefitAgreements/{agreementId}``
(specs/04 §4.6)."""

from __future__ import annotations

from typing import Any, Optional

from . import refs


def ref(client, agreement_id: str):
    """``DocumentReference`` for ``benefitAgreements/{agreement_id}``."""
    return refs.doc(client, refs.BENEFIT_AGREEMENTS, agreement_id)


def get(client, agreement_id: str) -> Optional[dict[str, Any]]:
    """Read the benefit-agreement document as dict-with-id, or ``None``."""
    return refs.get(client, refs.BENEFIT_AGREEMENTS, agreement_id)


def list_for_loan(client, loan_id: str) -> list[dict[str, Any]]:
    """All benefit agreements for a loan (``benefitAgreements where loanId ==``)."""
    query = client.collection(refs.BENEFIT_AGREEMENTS).where(
        filter=refs.field_filter("loanId", "==", loan_id)
    )
    return refs.stream_to_dicts(query)
