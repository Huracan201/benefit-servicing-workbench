"use client";

// U7 — Loan & benefit account detail (specs/15 §15.3). The full-craft centerpiece: nine
// regions composed over the part-1 kit, every one reading the authoritative SOURCE docs
// (loan / borrower / agreement / schedule + attempts / exceptions / events / notes) — never
// the eventually-consistent projections (loanWorkbenches / summaries), which lag a completed
// command by seconds (specs/05 §5.7). Every WRITE goes through a `useCommand` handle → the
// typed command client (CQRS, specs/02 P1). After any command settles, these live
// subscriptions reflect the landed state on their own — the screen never refetches a
// projection to confirm a write.

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import Card from "@/components/Card";
import Skeleton from "@/components/Skeleton";
import AccountHeader from "@/components/loans/detail/AccountHeader";
import AttemptsCard from "@/components/loans/detail/AttemptsCard";
import BenefitAgreementCard from "@/components/loans/detail/BenefitAgreementCard";
import ContributionSchedule from "@/components/loans/detail/ContributionSchedule";
import EventsTimeline from "@/components/loans/detail/EventsTimeline";
import ExceptionsPanel from "@/components/loans/detail/ExceptionsPanel";
import KpiTiles from "@/components/loans/detail/KpiTiles";
import NotesPanel from "@/components/loans/detail/NotesPanel";
import {
  useAttemptsForContribution,
  useBenefitAgreementDoc,
  useBorrowerDoc,
  useContributionsForAgreement,
  useEventsForEntity,
  useExceptionsForEntity,
  useLoanDoc,
  useNotesForLoan,
} from "@/lib/readAccount";
import { useSession } from "@/lib/session";

function BackLink() {
  return (
    <Link
      href="/loans"
      className="inline-flex items-center gap-1 text-xs font-medium text-ink-3 transition-colors hover:text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
    >
      <span aria-hidden="true">←</span> Loan portfolio
    </Link>
  );
}

// True for exactly the first committed render after `key` changes. The chained account
// subscriptions attach in an EFFECT (post-commit), so on the render where a parent id first
// resolves (loan → agreementId, or a newly-selected contribution id) the dependent hook
// still holds its stale "idle" snapshot — reporting `loading:false` with empty data. Gating
// each panel's loading on this bridges that single frame so an empty state never flashes
// before the query actually runs. The effect clears it in the same phase the real hook flips
// to `loading:true`, handing off the skeleton without a gap. Uses React's documented
// "adjust state during render" pattern (a converging setState), so it is Strict-Mode safe.
function useTransitionPending(key: string | null): boolean {
  const [pending, setPending] = useState(false);
  const [lastKey, setLastKey] = useState(key);
  if (lastKey !== key) {
    setLastKey(key);
    setPending(true);
  }
  useEffect(() => {
    setPending(false);
  }, [key]);
  return pending;
}

export default function LoanAccountPage({
  params,
}: {
  params: { loanId: string };
}) {
  const { loanId } = params;
  const { role } = useSession();

  // Chain the SOURCE subscriptions: loan → borrower / agreement → schedule; each hook is
  // idle until its id resolves. All hooks run unconditionally (stable order) before any
  // loading/empty/error return below.
  const loan = useLoanDoc(loanId);
  const borrowerId = loan.data?.borrowerId ?? null;
  const agreementId = loan.data?.benefitAgreementId ?? null;

  const borrower = useBorrowerDoc(borrowerId);
  const agreement = useBenefitAgreementDoc(agreementId);
  const contributions = useContributionsForAgreement(agreementId);
  const exceptions = useExceptionsForEntity(loanId);
  const events = useEventsForEntity(loanId);
  const notes = useNotesForLoan(loanId);

  const [selectedId, setSelectedId] = useState<string | null>(null);

  // Auto-select the contribution most worth inspecting once the schedule loads: a failed /
  // retry-pending installment, else the latest one that has an attempt. Runs only while
  // nothing is selected, so an operator's manual selection is never overridden.
  useEffect(() => {
    if (selectedId != null) return;
    const list = contributions.data;
    if (list.length === 0) return;
    const attention = list.find(
      (c) => c.status === "FAILED" || c.status === "RETRY_PENDING",
    );
    const withAttempts = [...list].reverse().find((c) => c.attemptCount > 0);
    const chosen = attention ?? withAttempts ?? null;
    if (chosen) setSelectedId(chosen.id);
  }, [contributions.data, selectedId]);

  const selectedContribution = useMemo(
    () => contributions.data.find((c) => c.id === selectedId) ?? null,
    [contributions.data, selectedId],
  );
  const attempts = useAttemptsForContribution(selectedId);

  // Panels whose subscription id resolves AFTER the loan (the schedule + agreement, keyed on
  // agreementId) or after a selection (attempts, keyed on the contribution id) would briefly
  // render an empty state on the resolving frame; gate their loading so a skeleton shows
  // instead (see useTransitionPending). Panels keyed on loanId subscribe from first mount and
  // resolve in parallel with the loan, so they need no gate.
  const schedulePending = useTransitionPending(agreementId);
  const attemptsPending = useTransitionPending(selectedId);
  const benefitLoading = schedulePending || (agreementId != null && agreement.loading);

  // --- Loading / empty / error states (specs/15 §15.2) ---

  if (loan.loading) {
    return (
      <div className="space-y-4">
        <BackLink />
        <div role="status" aria-label="Loading account" className="space-y-2">
          <Skeleton className="h-8 w-64" />
          <Skeleton className="h-4 w-96" />
        </div>
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
          <Skeleton className="h-72 w-full lg:col-span-2" />
          <Skeleton className="h-72 w-full" />
        </div>
      </div>
    );
  }

  if (loan.error) {
    return (
      <div className="space-y-4">
        <BackLink />
        <div
          role="alert"
          className="rounded border border-critical/[0.4] bg-critical/[0.08] px-4 py-10 text-center"
        >
          <p className="font-display text-sm font-semibold text-critical">
            Couldn&rsquo;t load this account
          </p>
          <p className="mx-auto mt-1 max-w-md text-xs text-ink-2">
            {loan.error.message}
          </p>
        </div>
      </div>
    );
  }

  const loanData = loan.data;
  if (!loanData) {
    return (
      <div className="space-y-4">
        <BackLink />
        <Card title="Account not found">
          <p className="py-6 text-center text-sm text-ink-3">
            No loan exists for id{" "}
            <span className="font-mono text-ink-2">{loanId}</span>. It may have been
            removed, or the link is out of date.
          </p>
        </Card>
      </div>
    );
  }

  // --- Full account screen ---

  // The loan loaded, but any subordinate SOURCE subscription can still fail (a denied rule, a
  // missing index, a transient error). Aggregate those into one non-blocking banner so a failed
  // read stays visibly DISTINCT from a genuine empty state — on an ops screen driving payment /
  // exception decisions, "No operational exceptions" must never silently mean "the exceptions
  // query errored" (specs/15 §15.2).
  const subordinateErrors = (
    [
      ["Borrower", borrower.error],
      ["Benefit agreement", agreement.error],
      ["Contribution schedule", contributions.error],
      ["Payment attempts", attempts.error],
      ["Operational exceptions", exceptions.error],
      ["Servicing timeline", events.error],
      ["Notes", notes.error],
    ] as Array<[string, Error | null]>
  ).filter((entry): entry is [string, Error] => entry[1] != null);

  return (
    <div className="space-y-4">
      <BackLink />

      {subordinateErrors.length > 0 ? (
        <div
          role="alert"
          className="rounded border border-critical/[0.4] bg-critical/[0.08] px-4 py-3"
        >
          <p className="text-xs font-semibold text-critical">
            Some panels failed to load — the data shown below may be incomplete.
          </p>
          <ul className="mt-1 space-y-0.5 text-xs text-ink-2">
            {subordinateErrors.map(([label, err]) => (
              <li key={label}>
                <span className="font-medium text-ink">{label}:</span> {err.message}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <AccountHeader
        loan={loanData}
        borrower={borrower.data}
        agreement={agreement.data}
      />

      <KpiTiles
        agreement={agreement.data}
        contributions={contributions.data}
        exceptions={exceptions.data}
      />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <BenefitAgreementCard
            agreement={agreement.data}
            borrowerId={loanData.borrowerId}
            borrowerRevision={borrower.data?.revision}
            role={role}
            loading={benefitLoading}
          />
          <ContributionSchedule
            contributions={contributions.data}
            loading={contributions.loading || schedulePending}
            selectedId={selectedId}
            onSelect={setSelectedId}
            role={role}
          />
          <AttemptsCard
            contribution={selectedContribution}
            attempts={attempts.data}
            loading={attempts.loading || attemptsPending}
          />
          <ExceptionsPanel
            exceptions={exceptions.data}
            loading={exceptions.loading}
            role={role}
          />
        </div>

        <div className="space-y-4">
          <EventsTimeline events={events.data} loading={events.loading} />
          <NotesPanel
            notes={notes.data}
            loading={notes.loading}
            loanId={loanId}
            role={role}
          />
        </div>
      </div>
    </div>
  );
}
