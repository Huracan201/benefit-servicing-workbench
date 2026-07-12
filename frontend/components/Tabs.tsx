"use client";

// Tabs — an accessible tablist with optional count pills (U1 design). Controlled:
// the parent owns `active` and updates it in `onChange`. Arrow-key roving focus and
// aria-selected are wired so the active tab is not communicated by color alone.

import type { ReactNode } from "react";
import { useRef } from "react";

export interface TabItem {
  key: string;
  label: ReactNode;
  /** Optional count rendered as a pill after the label. */
  count?: number;
}

export interface TabsProps {
  tabs: TabItem[];
  active: string;
  onChange: (key: string) => void;
  /** Accessible label for the tablist. */
  ariaLabel?: string;
  className?: string;
}

export function Tabs({ tabs, active, onChange, ariaLabel, className }: TabsProps) {
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});

  function focusIndex(i: number) {
    const clamped = (i + tabs.length) % tabs.length;
    const key = tabs[clamped]?.key;
    if (key) {
      refs.current[key]?.focus();
      onChange(key);
    }
  }

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={[
        "flex items-center gap-1 border-b border-border",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {tabs.map((tab, i) => {
        const selected = tab.key === active;
        return (
          <button
            key={tab.key}
            ref={(el) => {
              refs.current[tab.key] = el;
            }}
            role="tab"
            type="button"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            onClick={() => onChange(tab.key)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight") {
                e.preventDefault();
                focusIndex(i + 1);
              } else if (e.key === "ArrowLeft") {
                e.preventDefault();
                focusIndex(i - 1);
              }
            }}
            className={[
              "-mb-px inline-flex items-center gap-2 border-b-2 px-3 py-2 text-sm font-medium transition-colors",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              selected
                ? "border-accent text-ink"
                : "border-transparent text-ink-2 hover:text-ink",
            ].join(" ")}
          >
            {tab.label}
            {typeof tab.count === "number" ? (
              <span
                className={[
                  "min-w-[1.25rem] rounded-pill px-1.5 py-0.5 text-center text-xs font-semibold tabular-nums",
                  selected
                    ? "bg-accent/[0.12] text-accent"
                    : "bg-ink/[0.06] text-ink-2",
                ].join(" ")}
              >
                {tab.count}
              </span>
            ) : null}
          </button>
        );
      })}
    </div>
  );
}

export default Tabs;
