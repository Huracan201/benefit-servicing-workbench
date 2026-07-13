// Timestamp formatting for the loan / benefit DETAIL screen (specs/README — all
// wall-clock rendering derives from SYSTEM_TIMEZONE, never UTC).
//
// The readAccount hooks return RAW Firestore documents, so every date-time field
// arrives as a Firestore `Timestamp` object (see the TIMESTAMP CAVEAT in
// lib/readAccount.ts) — NOT the ISO string the Command* response types declare. These
// helpers coerce either shape (Timestamp | ISO string | null) to a Date and render it
// in SYSTEM_TIMEZONE, so a consumer never calls a string method on a Timestamp.

import { SYSTEM_TIMEZONE } from "@/lib/readModels";
import type { FirestoreTimestamp } from "@/lib/types";

interface TimestampLike {
  toDate: () => Date;
}

function hasToDate(ts: unknown): ts is TimestampLike {
  return (
    typeof ts === "object" &&
    ts !== null &&
    typeof (ts as TimestampLike).toDate === "function"
  );
}

/** Coerce a Firestore Timestamp | ISO string into a Date (null if absent/unparseable). */
export function toDate(ts: FirestoreTimestamp | null | undefined): Date | null {
  if (ts == null) return null;
  if (hasToDate(ts)) return ts.toDate();
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

const MONTH_YEAR = new Intl.DateTimeFormat("en-US", {
  timeZone: SYSTEM_TIMEZONE,
  month: "short",
  year: "numeric",
});

const DAY = new Intl.DateTimeFormat("en-US", {
  timeZone: SYSTEM_TIMEZONE,
  month: "short",
  day: "numeric",
});

const FULL_DATE = new Intl.DateTimeFormat("en-US", {
  timeZone: SYSTEM_TIMEZONE,
  month: "short",
  day: "numeric",
  year: "numeric",
});

const DATE_TIME = new Intl.DateTimeFormat("en-US", {
  timeZone: SYSTEM_TIMEZONE,
  month: "short",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

/** "Aug 2026" — the installment period. */
export function formatMonthYear(ts: FirestoreTimestamp | null | undefined): string {
  const d = toDate(ts);
  return d ? MONTH_YEAR.format(d) : "—";
}

/** "Aug 1" — a scheduled/posted day. */
export function formatDay(ts: FirestoreTimestamp | null | undefined): string {
  const d = toDate(ts);
  return d ? DAY.format(d) : "—";
}

/** "Sep 3, 2025" — an agreement start / end date. */
export function formatDate(ts: FirestoreTimestamp | null | undefined): string {
  const d = toDate(ts);
  return d ? FULL_DATE.format(d) : "—";
}

/** "Jul 11, 09:12" — an event / attempt timestamp. */
export function formatDateTime(ts: FirestoreTimestamp | null | undefined): string {
  const d = toDate(ts);
  return d ? DATE_TIME.format(d) : "—";
}
