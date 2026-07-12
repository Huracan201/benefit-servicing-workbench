"""benefits — benefit-agreement lifecycle commands (specs/10).

Phase-2 domain command layer (specs/19 §19.2). Owns the activate-benefit
command (schedule generation run inline, no Cloud Task — specs/10 §10.1) and its
DRF endpoint ``POST /benefit-agreements/{agreementId}/activate``.
"""
