"""seed — deterministic demo data generator (specs/18).

``python manage.py seed_demo`` writes a fixed, re-runnable seed set to Firestore
(emulator-aware via ``common.firestore``) plus the three pinned demo users with
role custom claims. The Firestore **dataset** uses deterministic ids and
overwriting ``set`` writes, so re-running the command is idempotent and the
public demo self-heals (specs/18 §18.1). The demo **users** are keyed by their
Firebase-assigned uid (looked up by email — not a deterministic id); their
``users/{uid}`` mirror preserves ``createdAt`` and bumps ``revision`` on
re-seed rather than resetting the audit trail. No domain-command logic lives
here — it reuses the Phase-2 foundation seams (``repositories``,
``servicing.events`` enum, the exception severity map).
"""
