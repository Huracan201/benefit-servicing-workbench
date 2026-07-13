"use client";

// U6 — the loan-portfolio filter row. Composes the Slice-A kit (FilterBar /
// FilterSelect / ToggleChip / Button) and delegates ALL the "which combination is
// legal" logic to useLoanFilters (see that file for the index-discipline rationale).
//
// The kit's FilterSelect has no `disabled` prop, so an inert select is disabled the
// standards way — wrapped in a <fieldset disabled>, which natively disables the
// contained <select>, removes it from the tab order, and lets us dim it + attach a
// reason tooltip without rebuilding the kit component.

import { useId } from "react";
import {
  FilterBar,
  FilterSelect,
  ToggleChip,
  type FilterOption,
} from "@/components/FilterBar";
import Button from "@/components/Button";
import type { LoanFilterControls } from "@/components/loans/useLoanFilters";
import {
  BENEFIT_STATUSES,
  EMPLOYMENT_STATUSES,
  LOAN_STATUSES,
} from "@/lib/types";
import { statusMeta } from "@/components/statusMeta";

const toOptions = (values: readonly string[]): FilterOption[] =>
  values.map((v) => ({ value: v, label: statusMeta(v).label }));

const BENEFIT_OPTIONS = toOptions(BENEFIT_STATUSES);
const EMPLOYMENT_OPTIONS = toOptions(EMPLOYMENT_STATUSES);
const LOAN_OPTIONS = toOptions(LOAN_STATUSES);

const EXCEPTION_REASON =
  "Cleared while “Has open exception” is on: that view orders by openExceptionCount (specs/13 index 5) and has no composite index to combine with a filter (§13.2a).";
const LOAN_REASON =
  "Pick an employer or employment status first — loan status has no standalone portfolio index (specs/13).";

interface DisabledFieldProps {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
  disabled: boolean;
  reason: string;
  allLabel: string;
}

/** A kit FilterSelect that can be disabled (via a wrapping fieldset). */
function DisabledField({
  label,
  value,
  options,
  onChange,
  disabled,
  reason,
  allLabel,
}: DisabledFieldProps) {
  const id = useId();
  const reasonId = `${id}-reason`;
  return (
    <fieldset
      disabled={disabled}
      // `title` is a mouse-hover convenience only; the sr-only reason below (linked via
      // aria-describedby) is what exposes it to AT + keyboard users, who can't reach a
      // disabled control's title.
      title={disabled ? reason : undefined}
      aria-describedby={disabled ? reasonId : undefined}
      className={["m-0 border-0 p-0", disabled ? "cursor-not-allowed opacity-50" : ""]
        .filter(Boolean)
        .join(" ")}
    >
      <FilterSelect
        id={id}
        label={label}
        value={value}
        options={options}
        onChange={onChange}
        allLabel={allLabel}
      />
      {disabled ? (
        <span id={reasonId} className="sr-only">
          {reason}
        </span>
      ) : null}
    </fieldset>
  );
}

export interface PortfolioFiltersProps {
  controls: LoanFilterControls;
  employerOptions: FilterOption[];
  employersLoading: boolean;
}

export function PortfolioFilters({
  controls,
  employerOptions,
  employersLoading,
}: PortfolioFiltersProps) {
  const { state, disabled, setEmployer, setBenefit, setEmployment, setLoan, setHasException } =
    controls;

  const loanReason = state.hasException ? EXCEPTION_REASON : LOAN_REASON;

  return (
    <div className="space-y-2">
      <FilterBar
        actions={
          controls.anyActive ? (
            <Button variant="ghost" onClick={controls.reset}>
              Reset
            </Button>
          ) : null
        }
      >
        <DisabledField
          label="Employer"
          value={state.employerId}
          options={employerOptions}
          onChange={setEmployer}
          disabled={disabled.employer}
          reason={EXCEPTION_REASON}
          allLabel={employersLoading ? "Loading…" : "All"}
        />
        <DisabledField
          label="Benefit"
          value={state.benefitStatus}
          options={BENEFIT_OPTIONS}
          onChange={setBenefit}
          disabled={disabled.benefit}
          reason={EXCEPTION_REASON}
          allLabel="All"
        />
        <DisabledField
          label="Employment"
          value={state.employmentStatus}
          options={EMPLOYMENT_OPTIONS}
          onChange={setEmployment}
          disabled={disabled.employment}
          reason={EXCEPTION_REASON}
          allLabel="All"
        />
        <DisabledField
          label="Loan status"
          value={state.loanStatus}
          options={LOAN_OPTIONS}
          onChange={setLoan}
          disabled={disabled.loan}
          reason={loanReason}
          allLabel="All"
        />
        <ToggleChip
          label="Has open exception"
          active={state.hasException}
          onChange={setHasException}
        />
      </FilterBar>
      <p className="text-xs text-ink-3">
        Filters map to composite indexes on <code className="font-mono">loanWorkbenches</code>;
        unsupported combinations aren&rsquo;t offered (specs/13 &sect;13.2a).
      </p>
    </div>
  );
}

export default PortfolioFilters;
