"""seed — deterministic demo data generator (specs/18).

``python manage.py seed_demo`` writes a fixed, re-runnable seed set to Firestore
(emulator-aware via ``common.firestore``) plus the three pinned demo users with
role custom claims. All ids are deterministic and every write is an overwrite
``set`` so re-running the command is idempotent and the public demo self-heals
(specs/18 §18.1). No domain-command logic lives here — it reuses the Phase-2
foundation seams (``repositories``, ``servicing.events`` enum, the exception
severity map).
"""
