"use client";

// FilterBar — the worklist filter row (U1 design). A flex container that composes
// labeled enum <select>s (FilterSelect) and standalone toggle chips (ToggleChip),
// with an optional reset. Kept controlled + generic so any screen wires its own
// index-backed filters (specs/05). The bar itself is presentational; the parent owns
// state and re-subscribes on change.

import { useId, type ReactNode } from "react";

export interface FilterBarProps {
  children: ReactNode;
  /** Optional right-aligned slot (e.g. a result count or reset). */
  actions?: ReactNode;
  className?: string;
}

export function FilterBar({ children, actions, className }: FilterBarProps) {
  return (
    <div
      role="group"
      aria-label="Filters"
      className={[
        "flex flex-wrap items-center gap-2",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
      {actions != null ? (
        <div className="ml-auto flex items-center gap-2">{actions}</div>
      ) : null}
    </div>
  );
}

export interface FilterOption {
  value: string;
  label: string;
}

export interface FilterSelectProps {
  /** Visible label (also the accessible name). */
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (value: string) => void;
  /** Label for the "any" option prepended to the list (default: "All"). */
  allLabel?: string;
  /** Set false to omit the leading "all" option. */
  includeAll?: boolean;
  id?: string;
}

/** A compact labeled enum select. Emits the raw option value ("" == all). */
export function FilterSelect({
  label,
  value,
  options,
  onChange,
  allLabel = "All",
  includeAll = true,
  id,
}: FilterSelectProps) {
  // A stable, collision-free id even when two selects share the same label; an
  // explicit `id` prop still overrides for callers that need a known target.
  const generatedId = useId();
  const selectId = id ?? generatedId;
  return (
    <label
      htmlFor={selectId}
      className="inline-flex items-center gap-1.5 text-xs text-ink-2"
    >
      <span className="font-medium uppercase tracking-wide text-ink-3">{label}</span>
      <select
        id={selectId}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded-sm border border-border bg-surface-2 px-2 py-1 text-body text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
      >
        {includeAll ? <option value="">{allLabel}</option> : null}
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export interface ToggleChipProps {
  label: ReactNode;
  active: boolean;
  onChange: (active: boolean) => void;
  className?: string;
}

/** A standalone on/off filter chip (aria-pressed communicates state, not color). */
export function ToggleChip({ label, active, onChange, className }: ToggleChipProps) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={() => onChange(!active)}
      className={[
        "inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-xs font-medium transition-colors",
        "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
        active
          ? "border-accent/[0.4] bg-accent/[0.12] text-accent"
          : "border-border bg-surface-2 text-ink-2 hover:text-ink",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <span
        aria-hidden="true"
        className={[
          "h-1.5 w-1.5 rounded-full",
          active ? "bg-accent" : "bg-ink-3",
        ].join(" ")}
      />
      {label}
    </button>
  );
}

export default FilterBar;
