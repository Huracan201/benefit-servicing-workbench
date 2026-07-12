# Engineering Report — Phase 2 Part 2: Remaining Domain Commands

**Project:** BenefitServicing Workbench (`Huracan201/benefit-servicing-workbench`)
**Phase:** 2 — Domain commands, part 2 (completes the command layer; per [specs/19 §19.2](../19-delivery-and-scope.md))
**Scope:** benefit suspend/resume/terminate · employment-status cascade · exception workflow · servicing notes · admin (role + employer status)
**Status:** ✅ Built & QA-verified on `release/phase-2-part-2` (base `main` `c6671ce`) — pending commit → CI → merge
**Date:** 2026-07-12

---

## 1. Summary

Part 2 completes the Phase-2 **domain command layer** by adding the commands part 1 deferred — the benefit lifecycle, the employment cascade, and the operational workflows (exceptions, notes, admin). Because the foundation (repositories, state machines, idempotency, events, exceptions) already existed, this slice is composition — but it introduced the system's first **unbounded post-commit work** (cancel-future-contributions, schedule-shift), which is where the hardest QA finding lived.

The headline outcome isn't a feature — it's a **crash-recovery hardening**. The second-pass QA (three independent agents + adversarial tracing) found that the lifecycle commands completed their idempotency record *inside* the core transaction and then ran the unbounded tail *after* commit, so a transient error mid-tail could permanently strand data with no retry. That's the **inline analog of the two-phase payment crash gap**, and it was fixed properly (not deferred) before anything was committed.

**Headline outcomes**
- 25 new backend modules (~4,000 lines) across 5 command areas + 2 shared inline helpers; 6 existing files extended.
- The **benefit lifecycle** (suspend/resume/terminate) with cumulative schedule-shift on resume and bounded cancel-future on terminate.
- The **employment cascade** (LEAVE→suspend, TERMINATED→terminate, return→resume) with correct LEAVE-vs-MANUAL semantics and idempotent no-ops.
- Exception workflow, notes, and admin commands; `/api/v1` wiring for all endpoints.
- **1 BLOCKER + 3 HIGH + 4 MEDIUM + several LOW** found in QA — **all fixed and independently verified** before commit.

---

## 2. Scope

**In scope (completes Phase 2)**
- **Benefit lifecycle**: suspend (`ACTIVE→SUSPENDED`, `suspendedReason=MANUAL`), resume (`SUSPENDED→ACTIVE` + inline schedule-shift, specs/07 §7.8), terminate (`→TERMINATED` + inline cancel-future). specs/10 §10.2–10.3.
- **Employment cascade**: `change_employment_status` cascading to the benefit (LEAVE→suspend reason LEAVE; TERMINATED→terminate + cancel-future; return→resume only when `suspendedReason==LEAVE`, + shift). Idempotent no-op when already at/past target. specs/10 §10.4, specs/06 §6.7.
- **Exception workflow**: create (manual), assign (status-neutral), mark-in-review, resolve, dismiss. specs/06 §6.4, specs/09 §9.3.
- **Notes**: append-only servicing note. specs/10 §10.5.
- **Admin**: set user role (claim + mirror + event), set employer status (`ACTIVE↔INACTIVE`). specs/12 §12.3, specs/06 §6.6a.
- **Shared inline helpers**: `cancel_future_contributions`, `shift_schedule` (bounded, idempotent).

**Later**: Phase 3 moves the inline helpers onto durable **Cloud Tasks** (which is the real, durable fix for the crash gap — see §9); read-model projections; the UI (Phase 4).

---

## 3. What was delivered

| Area | Module(s) | Command(s) |
|------|-----------|-----------|
| Benefit lifecycle | `benefits/services.py` (+views), `benefits/shift.py` | suspend / resume / terminate |
| Employment | `employment/` | change employment status + cascade |
| Exceptions | `exceptions/commands.py` (+views) | create / assign / mark-in-review / resolve / dismiss |
| Notes | `notes/` | add servicing note |
| Admin | `administration/` | set user role / set employer status |
| Shared helpers | `contributions/lifecycle.py` | cancel-future-contributions; (shift lives in `benefits/shift.py`) |
| Wiring | `api/urls.py`, `config/settings.py` | `/api/v1` routes + 3 new apps |

Every command mirrors the part-1 reference patterns (`activate_benefit` idempotency-first ordering; the finalize-guard discipline) and composes the frozen `common/` core.

---

## 4. Architecture highlights

**The crash-gap hardening (the load-bearing change).** The lifecycle commands do a bounded core transaction (the status change) followed by *unbounded* post-commit work (cancel N contributions / shift N installments). Completing idempotency in the core transaction meant a replay skipped the tail — so a transient error mid-tail was unrecoverable. The fix moves to a **two-phase-complete envelope**:

```text
core txn (transition; idempotency stays PENDING) → commit → inline tail → complete-key txn
```

A failure in the tail leaves the record `PENDING`, so a same-key retry (after lease expiry) **reclaims and re-drives** the tail — which is idempotent (status-guarded). The transition is reclaim-aware: on a reclaim where the benefit is already in the target state, it skips the (already-applied) transition and re-runs only the tail; a genuine different-key command on an already-terminated benefit still `409`s. This is the same recovery principle as the part-1 two-phase payment, adapted to the inline lifecycle tasks.

**Cascade idempotency.** The employment cascade is a guarded no-op when the benefit is already at/past target (LEAVE onto an already-`SUSPENDED` benefit doesn't overwrite a `MANUAL` reason; return-to-`ACTIVE` auto-resumes only a `LEAVE`-suspended benefit). One idempotency key spans the borrower update + the benefit cascade + the post-commit tail.

**Cumulative schedule-shift.** Resume anchors the schedule to a persisted `scheduleShiftMonths` witness so multiple suspend/resume cycles accumulate, while a re-driven shift recomputes identical (forward-only) targets — idempotent under both re-run and repeated suspension.

---

## 5. Process — build and two-pass QA

1. **Build** — an 11-agent workflow across 5 dependency-ordered phases (shared helpers → parallel commands → employment cascade → wiring+tests → in-workflow review). The review phase found 1 HIGH + 2 MED, fixed and lead-verified.
2. **Independent second-pass QA** — three fresh review agents (lifecycle/cascade correctness · exceptions/notes/admin + data fidelity · CI + test efficacy) plus lead adversarial tracing of the post-commit-task pattern. This is where the BLOCKER surfaced.
3. **Consolidated fix** — two parallel fixers on disjoint files (the crash-gap envelope; everything else) + a lead parity fix, each **driven through an in-memory Firestore fake** and then independently lead-verified line-by-line.

The layered approach earned its keep again: the crash-gap BLOCKER was invisible to compile checks and the in-workflow review; it required tracing the commit→tail→replay path against the idempotency contract.

---

## 6. Verification & tests

| Check | Where | Result |
|-------|-------|--------|
| Full backend `py_compile` | offline | ✅ |
| Read-before-write in every transactional fn (15 changed files) | offline (traced) | ✅ none violated |
| Crash-gap envelope (4 scenarios: first-call / replay / reclaim-after-failure / different-key-409) | offline (fake + code read) | ✅ |
| `_months_between` month-arithmetic (incl. the date-bomb clamp cases) | offline (executed) | ✅ |
| `common/` regression | offline | ✅ 60/60 |
| Emulator integration — 5 flows (suspend/resume/terminate, cascade, exceptions, notes) | **CI runner** | pending run |
| `manage.py check` + Django boot (3 new apps) | **CI runner** | pending run |

**Tests added:** 4 new emulator integration test files (all 5 flows, concrete post-state assertions — the shift dates + endDate, the cancellations, the cascade branches, the count changes) + 10 pure `@tag('unit')` tests for the shift month-arithmetic (closing the pt2 unit-coverage gap and locking the date-bomb edge cases).

---

## 7. Issues found & fixed (before commit)

All found in second-pass QA; all fixed and lead-verified.

| # | Severity | Issue | Resolution |
|---|----------|-------|-----------|
| 1 | 🔴 **BLOCKER** | Lifecycle commands completed idempotency in the core txn, then ran the unbounded tail post-commit → a transient error mid-tail permanently stranded SCHEDULED contributions + orphaned an open exception, un-retried (replay skipped) | **Two-phase-complete**: idempotency stays PENDING across commit→tail; completed only after the tail; reclaim re-drives (terminate/resume/employment) |
| 2 | 🟠 HIGH | Same root cause on **resume** → a lost shift = the forbidden catch-up lump + inconsistent state | Same envelope fix |
| 3 | 🟠 HIGH | **Date-bomb test**: hard-coded a 2-month shift; production rounds partial months up → CI red on Jan 31 / Apr 29–30 / Aug 31 | Test derives the expected shift from persisted `scheduleShiftMonths` |
| 4 | 🟡 MED | `shift_schedule` used a non-transactional batch with status-unguarded blind updates → could rewrite `scheduledDate` on a POSTED doc under concurrency | Rewritten as per-batch transactions with in-txn re-read + `status==SCHEDULED` guard |
| 5 | 🟡 MED | Manual **dismiss lost operator attribution** (reused the actor-less writer) | Captures the event id + sets `resolvedBy` |
| 6 | 🟡 MED | Borrower/employer manual exceptions written **global-only** (not mirrored to the entity timeline) | Pointer populated from the target entity → mirrors correctly |
| 7 | 🟡 MED | Cross-path **`openExceptionCount` undercount** + resolution overwrite (a manually-resolved exception re-decremented by the payment/cancel paths) — reached into merged part-1 `payments/service.py` | Gated the decrement + auto-resolve/dismiss on the exception still being OPEN/IN_REVIEW, across **all 3 payment finalize paths + the cancel batch** |
| 8 | 🟢 LOW ×6 | re-drive summary-event dedup; notes over-stamped; `set_user_role` mapped all Auth errors to 404; unused import; zero pt2 unit coverage; multi-loan cascade | Fixed (multi-loan documented as the MVP one-loan-per-borrower constraint) |

**Cleared, not bugs:** the exception state machine + count symmetry (all decrements clamped + `assert_transition`-guarded); assign is genuinely status-neutral; every eventType is in the closed enum; the LEAVE-vs-MANUAL guard; the cumulative-shift math; and (re-confirmed) no read-after-write anywhere.

---

## 8. Key decisions

- **Harden the crash gap now** rather than defer to Phase 3. The durable fix is Cloud Tasks (Phase 3), but the two-phase-complete envelope makes a transient error **recoverable by retry** instead of silent data loss — a real correctness property worth having in the merged code.
- **The count bug fix reaches into merged code** (`payments/service.py`): the new manual-resolve path exposed a latent undercount in part 1. Fixing it there (not just working around it) keeps the accounting correct system-wide.
- **Inline tasks, unchanged interface** (`TASK_EXECUTION_MODE=inline`): the same helper functions move onto Cloud Tasks in Phase 3 with no logic change; the envelope fix means that migration inherits durable recovery for free.

---

## 9. Known limitations (by design)

- **Recovery still needs a client retry** until Phase 3. The envelope keeps a failed operation `PENDING` (recoverable), but no reaper runs in the Phase-2 inline model — a same-key retry (or Phase-3's durable Cloud Task + lease reaper) is what completes it. Documented, not silent.
- **One loan per borrower** in the employment cascade (MVP constraint; multi-loan is a data-model extension).
- Manual borrower/employer exceptions mirror to the timeline but leave the denormalized `*Name` fields null (avoids an extra lookup at MVP scale).
- Read-model projections still not written (Phase 3).

---

## 10. What's next

Commit → PR → CI (the real emulator run for all 5 flows) → CodeRabbit → merge. That completes the **Phase-2 command layer**. Then **Phase 3**: move the inline helpers onto Cloud Tasks + Cloud Scheduler (durable retry closes the crash gap fully), add the reconciliation/reaper jobs, and build the read-model projections.

---

*Related: [phase-2-command-layer.md](./phase-2-command-layer.md) (part 1) · [specs/08 §8.2–8.3](../08-idempotency-and-consistency.md) (idempotency + lease) · [specs/10 §10.2–10.4](../10-benefit-and-employment-workflows.md) · [specs/07 §7.8](../07-financial-rules.md) (schedule shift).*
