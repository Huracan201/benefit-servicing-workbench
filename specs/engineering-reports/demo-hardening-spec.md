# Spec — Demo-hardening pass ("get to 9")

**Status:** 📋 Spec — the build follows this. Derived from the [full system review](#) (2026-07-13): production-readiness **5/10**, demo/portfolio strength **8/10**.

**Objective.** Close the specific findings that hold the demo/portfolio score at 8 (and dent production-readiness): the three "tells" a staff-level reviewer lands on in the first read — a *claimed-but-not-enforced* correctness guarantee, a *built-but-unwired* observability layer, and visible *frontend finish seams* — plus the front-door doc/contract inaccuracies. This is a correctness + observability + polish pass, distinct from Phase 6 (deploy IaC).

**Non-goals.** Cloud deploy / IaC (Phase 6); the projection-scale rework, the reaper re-drive, and the fleet-wide throttle (tracked follow-ups).

---

## W1 — Enforce `If-Match` optimistic concurrency server-side (the headline correctness gap)

**Problem.** `If-Match` / `expectedRevision` is spec-mandated (specs/09, [08 §8.4]), sent by the command client, and prominently scoped — but the header is never read server-side, `commands.base.StaleWrite` is never raised, and the only test (`frontend/e2e/stale-write.spec.ts`) *mocks* the 409. A green test over an absent feature.

**Design.**
- `commands/base.py`: add `expected_revision: int | None = None` to `CommandContext` (+ its `build(...)`), and a pure helper `assert_expected_revision(entity: dict, ctx: CommandContext)` that raises `StaleWrite` when `ctx.expected_revision is not None and entity["revision"] != ctx.expected_revision`.
- The shared view helper `_build_ctx` (benefits/views.py, exceptions/views.py) parses `request.headers.get("If-Match")` → `int` (a malformed value → 400 `INVALID_IF_MATCH`) → `CommandContext.build(expected_revision=…)`.
- Inside the transition transaction, **after** the entity is read, call `assert_expected_revision(...)`: `suspend_benefit` / `resume_benefit` / `terminate_benefit` (against `agreement["revision"]`) and `change_employment_status` (against `borrower["revision"]` — that endpoint targets the borrower). The check is inside the txn, so it races correctly against concurrent writers.

**Acceptance.**
- A stale `If-Match` on suspend/resume/terminate/employment → `409 STALE_WRITE`; a matching or absent `If-Match` → proceeds.
- Unit test for `assert_expected_revision` (match / mismatch / None). Emulator test (`@tag('emulator')`) that suspends with a stale revision → 409, no state change.
- `frontend/e2e/stale-write.spec.ts` **de-mocked**: it sends a stale `If-Match` and asserts a *server-produced* 409 (the `page.route` 409 fabrication is deleted).

## W2 — Wire the structured-logging layer live on the money path

**Problem.** `core.logging_utils.log_event` + the operational-field whitelist + idempotency-key hashing exist and `LOGGING` is applied, but there are **zero call sites** — the two-phase payment / command path emits nothing, so an incident is reconstructed from the audit trail, not telemetry.

**Design.**
- Shared `_respond`: wrap the command call with a monotonic timer and emit one completion `log_event` — `operation`, `entityId`, `result` (`OK` / `IN_PROGRESS` / the `errorCode`), `durationMs`, `correlationId`, hashed `idempotencyKey` — on success, 202, and `CommandError`. Uniform across every command.
- `payments/service.py`: emit money-path lifecycle lines at charge-start and finalize (`operation=PROCESS_CONTRIBUTION`, `result=POSTED|FAILED`, `entityId=contributionId`, `durationMs`).

**Acceptance.** Every command emits a JSON completion line with the operational fields; a processed/failed payment emits a finalize line with the outcome. Verified by a unit/log-capture test asserting the fields are present + the idempotency key is hashed (never raw).

## W3 — Frontend finish pass (remove the dead affordances)

**Problem.** A reviewer meets these in the first clicks: a "View as" role switcher + a `RoleGate` component that **nothing consumes** (screens gate on `useSession().role`), fabricated nav count badges, and a non-functional global search.

**Design.**
- Delete the "View as" switcher from `TopBar` and delete `components/RoleGate.tsx` (dead). Role demonstration is via signing in as the seeded `ops@ / mgr@ / admin@` personas.
- Nav badges: **wire to real read-model counts** (open-exception count from `portfolioSummaries/current`; payments-needing-action if exposed) via a live subscription in `Nav`; render nothing while loading — no fabricated numbers.
- Remove the non-functional top-bar search; the loan-portfolio `FilterBar` is the real search surface.

**Acceptance.** No control renders that does nothing. Nav badges show real counts or nothing. `npm run typecheck/lint/build` green.

## W4 — Front-door accuracy (doc + contract)

**Problem.** `invariants.py`'s docstring claims I1–I7 are enforced in-txn, but only I1–I4 are called. `README.md` / `CLAUDE.md` status headers are ~1.5 phases stale. The "authoritative" `openapi.yaml` advertises a `GET /benefit-agreements/{id}` with no implementation (+ an orphaned `Loan` schema).

**Design.**
- `common/invariants.py`: correct the docstring to state I1–I4 are the enforced set (I5–I7 are available helpers, not wired).
- `README.md` + `CLAUDE.md`: refresh the status to "Phases 1–5 merged; Phase 6 (deploy) is the single remaining phase."
- `specs/openapi.yaml`: delete the phantom `GET /benefit-agreements/{id}` path + the orphaned `Loan` schema (reads are Firestore subscriptions per CQRS — no read GETs exist). Keep Spectral green.

**Acceptance.** Docstring matches the code; the status headers match the engineering-reports index; `npx spectral lint specs/openapi.yaml` green with no orphaned-schema warning.

---

## Verification & delivery

Backend verified by running (venv): `manage.py check` + the new unit tests + the `@tag('unit')` suite. Frontend: `npm run typecheck/lint/build`. The emulator test + the de-mocked e2e + Spectral run on CI. Shipped on `release/demo-hardening` → PR → CI → CodeRabbit → merge. A short follow-up report records the before/after against the two review scores.
