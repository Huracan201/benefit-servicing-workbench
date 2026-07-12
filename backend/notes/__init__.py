"""notes — servicing-note command (specs/10 §10.5).

Phase-2 domain command layer (specs/19 §19.2). Owns the append-only
add-servicing-note command (``loans/{loanId}/notes/{noteId}``) and its DRF
endpoint ``POST /loans/{loanId}/notes``. Notes are timestamped, attributed to
the authenticated user, non-empty, and never editable or deletable (append-only,
consistent with the audit model).
"""
