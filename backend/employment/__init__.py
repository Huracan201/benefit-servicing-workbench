"""employment — the borrower employment-status change command (specs/10 §10.4).

Phase-2 domain command layer (specs/19 §19.2). Owns the change-employment-status
command and its benefit-status cascade (LEAVE → suspend, TERMINATED → terminate +
cancel-future, return-from-LEAVE → resume + schedule-shift) and the DRF endpoint
``POST /borrowers/{borrowerId}/employment-status``.
"""
