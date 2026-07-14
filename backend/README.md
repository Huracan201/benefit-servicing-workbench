# Backend — BenefitServicing Workbench

Django + Django REST Framework API. Firestore (Native mode) is the only
datastore — there are **no Django ORM models**, and `django.contrib`
admin/auth/sessions/contenttypes are intentionally not installed. The ORM is
wired to a dummy in-memory sqlite that is never used, purely so Django's own
management machinery works. Identity is Firebase Auth (ID tokens verified via
the Admin SDK); authorization is role-based via Firebase custom claims.

See [specs/02](../specs/02-architecture.md) (architecture),
[specs/21](../specs/21-deployment-and-operations.md) (config/ops), and
[specs/16](../specs/16-observability.md) (logging).

## Layout

```
backend/
├── config/            # settings, urls, wsgi/asgi, JSON logging  (this module)
│   ├── settings.py
│   ├── urls.py
│   ├── wsgi.py  asgi.py
│   └── logging.py     # structured JSON formatter (specs/16 §16.2)
├── common/            # money, periods, ids, state machines, invariants, firestore client
├── firebase_auth/     # Firebase token auth, role permissions, internal OIDC middleware
├── core/              # correlation-id middleware, health/readiness views, Firestore schema
├── manage.py
├── requirements.txt
└── .env.example
```

`config.settings` is the `DJANGO_SETTINGS_MODULE`. Installed apps:
`rest_framework, corsheaders, common, firebase_auth, core`.

## Configuration

All config is read from the environment. Copy `.env.example` to `.env` for local
dev; every variable and its `local` / `ci` / `demo` values are in
[specs/21 §21.3](../specs/21-deployment-and-operations.md). Key points:

- Money is integer US cents; `SYSTEM_TIMEZONE` (default `America/New_York`) is
  the business calendar for period labels and `scheduledDate`.
- `FIRESTORE_EMULATOR_HOST` present ⇒ offline/dev mode (emulator-aware client,
  dev-secret `/internal/*` auth, `TASK_EXECUTION_MODE=inline` by default).

## Local development

One command from the repo root brings up the whole stack **with seeded demo
data** — `make demo` (Phase 6; the same emulator + `seed_demo` + Django + Next.js
harness the CI e2e job runs).

Manual (needs the Firebase emulator running — see
[firebase/emulator/README.md](../firebase/emulator/README.md)):

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' .env.example | xargs)   # or use your own .env
python manage.py check
gunicorn config.wsgi:application --bind 0.0.0.0:8000 --reload
```

Operational endpoints: `GET /health` (liveness), `GET /readiness`
(dependencies reachable). `/api/v1/` (business commands, Phase 2) and
`/internal/` (OIDC-gated Cloud Tasks + Scheduler handlers, Phase 3) are wired;
fire a scheduler job locally with `manage.py run_job <name>`.

## Tests

- Pure-Python core (no third-party deps):
  ```bash
  cd backend && python -m unittest discover -s common/tests -p 'test_*.py' -t .
  ```
- Full suite (CI): `python manage.py test --tag=unit`, plus emulator-tagged
  integration tests run under `firebase emulators:exec`.
