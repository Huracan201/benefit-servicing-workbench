# Firebase Emulator Suite — local & CI

Runs Firebase **Auth** + **Firestore** (with our `firestore.rules` and `firestore.indexes.json`) fully offline, for local development, the emulator integration tests, and the security-rule tests ([specs/17-testing.md](../../specs/17-testing.md), [specs/12-auth-and-security.md §12.6](../../specs/12-auth-and-security.md)).

> Cloud Tasks and Cloud Scheduler have **no** Firebase emulator. Their handlers are plain Django endpoints ([specs/14](../../specs/14-async-and-background-jobs.md)); locally you invoke them directly (or via the seed/test harness) rather than through an emulated queue.

## Prerequisites
- Node.js 20 (matches CI)
- **Java JDK 11+** (the Firestore emulator is a Java process)
- `firebase-tools`: `npm i -g firebase-tools` (or run via `npx firebase-tools`)

## Project id
Emulator/test runs use the **`demo-`-prefixed** project so no real GCP credentials are needed — Firebase treats any `demo-*` project id as offline. Aliases are in [`.firebaserc`](../.firebaserc):

- `default` → `benefitservicing-workbench` — replace with your real GCP project id for `firebase deploy`.
- `demo` → `demo-benefitservicing-workbench` — offline emulator/CI.

## Start the emulator
Run from the `firebase/` directory (that's where `firebase.json` lives, per the repo layout in [specs/02 §2.5](../../specs/02-architecture.md)):

```bash
cd firebase
firebase emulators:start --project=demo-benefitservicing-workbench
```

| Emulator | URL |
|----------|-----|
| Emulator UI | http://localhost:4000 |
| Firestore | localhost:8080 |
| Auth | localhost:9099 |

To reuse seeded state across restarts (import on start, export on exit):

```bash
firebase emulators:start --project=demo-benefitservicing-workbench \
  --import=./emulator/data --export-on-exit=./emulator/data
```

Exported data under `./emulator/data` is git-ignored — regenerate it with the seed script rather than committing a snapshot.

## Point the app, backend, and tests at the emulator
Set these env vars in the process that talks to Firebase (Next.js dev server, Django, test runner):

```bash
export FIRESTORE_EMULATOR_HOST="localhost:8080"
export FIREBASE_AUTH_EMULATOR_HOST="localhost:9099"
export GCLOUD_PROJECT="demo-benefitservicing-workbench"      # Admin SDK / google-cloud clients
export NEXT_PUBLIC_FIREBASE_PROJECT_ID="demo-benefitservicing-workbench"
export NEXT_PUBLIC_USE_FIREBASE_EMULATOR="true"              # frontend connects via connect*Emulator()
```

The Firebase Admin SDK (Django) and the `google-cloud-firestore` client auto-route to the emulator when `FIRESTORE_EMULATOR_HOST` is set — no service-account key required offline.

## Seed data
Run the deterministic seed script ([specs/18-seed-and-demo.md](../../specs/18-seed-and-demo.md)) against a running emulator (env vars above set), which also creates the demo users **with their custom-claim roles** via the Auth emulator:

```bash
# from backend/, with the emulator running and env vars exported
python manage.py seed_demo --emulator
```

## Tests
- **Security-rule tests** ([specs/12 §12.6](../../specs/12-auth-and-security.md)) live in [`../tests/firestore-rules.test.ts`](../tests/firestore-rules.test.ts) and use `@firebase/rules-unit-testing` against the Firestore emulator, asserting: every protected collection is unreadable without a role claim, unwritable by any client, readable with a claim; `users` self-write denied; `idempotencyKeys` fully client-invisible. Install deps once, then run from the `firebase/` directory:

  ```bash
  cd firebase && npm install       # first time only
  npm run test:rules:ci            # wraps: firebase emulators:exec --only firestore "vitest run"
  # ...or, if the emulator is already running:
  npm run test:rules
  ```

- **Emulator integration tests** (transactions, the concurrency gate, reconciliation, bounded batches, projections — [specs/17 §17.2](../../specs/17-testing.md)) run the backend suite inside `emulators:exec` so the emulator starts/stops around the tests:

  ```bash
  firebase emulators:exec --project=demo-benefitservicing-workbench \
    "python ../backend/manage.py test --tag=emulator"
  ```

CI uses the same `emulators:exec` wrapper so the emulator lifecycle is tied to the test run ([specs/17 §17.5](../../specs/17-testing.md)).

## Deploying rules & indexes (not the emulator)
```bash
firebase deploy --only firestore:rules,firestore:indexes --project=<real-project>
```
Rules and indexes are the source of truth in this folder; never edit them in the console ([specs/02 §2.6](../../specs/02-architecture.md), [specs/13](../../specs/13-firestore-indexes.md)).
