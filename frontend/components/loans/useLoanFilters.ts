"use client";

// U6 — Loan-portfolio filter state + the index-backed predicate discipline
// (specs/13 §13.2a). This hook is the ONE place that decides which filter
// combinations are legal, because every combination must be served by a composite
// index on `loanWorkbenches` (firebase/firestore.indexes.json) — an unsupported
// where/orderBy set fails at query time, not in review.
//
// The supported shapes (each maps to exactly one index — see specs/13 §13.1):
//   S1  { employerId, benefitStatus?, loanStatus? }   → (employerId,benefitStatus,loanStatus) / (employerId,loanStatus)
//   S2  { employmentStatus, loanStatus? }             → (employmentStatus,loanStatus)
//   S3  { benefitStatus }                             → single-field equality (default __name__ order)
//   S4  {}  + sort "openExceptions"                   → (openExceptionCount,updatedAt)  ← the "Has open exception" toggle
//
// Rules that fall out of that table and are enforced here:
//   • employer-lane and employment-lane are mutually exclusive (no composite covers
//     employerId + employmentStatus); picking one clears the other.
//   • benefitStatus rides the employer lane (S1) or stands alone (S3) but NOT the
//     employment lane (S2 has no benefitStatus) — picking one clears the other.
//   • loanStatus is a *refinement*: it only has an index inside an employer or
//     employment selection, never on its own — so its control is disabled until a
//     host lane exists.
//   • the "Has open exception" view orders by openExceptionCount and has NO composite
//     to combine with any equality filter (§13.2a calls out employer specifically) —
//     turning it on clears + disables every dropdown; touching a dropdown turns it off.
//
// Setters keep state permanently on one of S1–S4, so `resolved` is always index-backed
// and the query never errors from a bad combination. Note we sort with "default"
// (implicit __name__) rather than "nextContribution" for benefit-only, so loans whose
// nextContributionDate is null (suspended / terminated / pending) are NOT dropped by
// the orderBy — a plain benefit-status filter must return them.

import { useCallback, useMemo, useState } from "react";
import type { LoanWorkbenchFilters } from "@/lib/readModels";
import type { BenefitStatus, EmploymentStatus, LoanStatus } from "@/lib/types";

export interface LoanFilterState {
  employerId: string;
  benefitStatus: string;
  employmentStatus: string;
  loanStatus: string;
  hasException: boolean;
}

export interface LoanFilterDisabled {
  employer: boolean;
  benefit: boolean;
  employment: boolean;
  loan: boolean;
}

export interface LoanFilterControls {
  state: LoanFilterState;
  /** The (always index-backed) query passed to useLoanWorkbenchesPage. */
  resolved: LoanWorkbenchFilters;
  /** A stable key that changes iff the effective query changes (pagination reset). */
  key: string;
  /** Which selects are currently inert and must render disabled. */
  disabled: LoanFilterDisabled;
  anyActive: boolean;
  setEmployer: (v: string) => void;
  setBenefit: (v: string) => void;
  setEmployment: (v: string) => void;
  setLoan: (v: string) => void;
  setHasException: (on: boolean) => void;
  reset: () => void;
}

const EMPTY: LoanFilterState = {
  employerId: "",
  benefitStatus: "",
  employmentStatus: "",
  loanStatus: "",
  hasException: false,
};

export function useLoanFilters(): LoanFilterControls {
  const [state, setState] = useState<LoanFilterState>(EMPTY);

  // Employer lane: clears the employment lane; keeps benefit; keeps loan only while a
  // host (this employer) exists.
  const setEmployer = useCallback((v: string) => {
    setState((s) => ({
      ...s,
      hasException: false,
      employerId: v,
      employmentStatus: "",
      loanStatus: v ? s.loanStatus : "",
    }));
  }, []);

  // Employment lane: clears employer + benefit (S2 carries neither); keeps loan only
  // while this employment value is the host.
  const setEmployment = useCallback((v: string) => {
    setState((s) => ({
      ...s,
      hasException: false,
      employmentStatus: v,
      employerId: "",
      benefitStatus: "",
      loanStatus: v ? s.loanStatus : "",
    }));
  }, []);

  // Benefit: valid alongside an employer (S1) or standalone (S3). If the employment
  // lane is active with no employer, choosing a benefit switches to standalone S3.
  const setBenefit = useCallback((v: string) => {
    setState((s) => {
      if (v && s.employmentStatus && !s.employerId) {
        return { ...s, hasException: false, benefitStatus: v, employmentStatus: "", loanStatus: "" };
      }
      return { ...s, hasException: false, benefitStatus: v };
    });
  }, []);

  // Loan is only reachable when a host lane is selected (its control is disabled
  // otherwise), so it never becomes the sole filter.
  const setLoan = useCallback((v: string) => {
    setState((s) => ({ ...s, hasException: false, loanStatus: v }));
  }, []);

  const setHasException = useCallback((on: boolean) => {
    setState((s) => (on ? { ...EMPTY, hasException: true } : { ...s, hasException: false }));
  }, []);

  const reset = useCallback(() => setState(EMPTY), []);

  const resolved = useMemo<LoanWorkbenchFilters>(() => {
    if (state.hasException) return { sort: "openExceptions" };
    if (state.employerId) {
      return {
        employerId: state.employerId,
        ...(state.benefitStatus ? { benefitStatus: state.benefitStatus as BenefitStatus } : {}),
        ...(state.loanStatus ? { loanStatus: state.loanStatus as LoanStatus } : {}),
        sort: "default",
      };
    }
    if (state.employmentStatus) {
      return {
        employmentStatus: state.employmentStatus as EmploymentStatus,
        ...(state.loanStatus ? { loanStatus: state.loanStatus as LoanStatus } : {}),
        sort: "default",
      };
    }
    if (state.benefitStatus) {
      return { benefitStatus: state.benefitStatus as BenefitStatus, sort: "default" };
    }
    return { sort: "default" };
  }, [state]);

  const disabled = useMemo<LoanFilterDisabled>(() => {
    if (state.hasException) {
      return { employer: true, benefit: true, employment: true, loan: true };
    }
    return {
      employer: false,
      benefit: false,
      employment: false,
      // loanStatus has no standalone index — needs an employer or employment host.
      loan: !(state.employerId || state.employmentStatus),
    };
  }, [state]);

  const anyActive =
    state.hasException ||
    Boolean(state.employerId || state.benefitStatus || state.employmentStatus || state.loanStatus);

  const key = useMemo(() => JSON.stringify(resolved), [resolved]);

  return {
    state,
    resolved,
    key,
    disabled,
    anyActive,
    setEmployer,
    setBenefit,
    setEmployment,
    setLoan,
    setHasException,
    reset,
  };
}
