// Formatting helpers. Money is integer US cents everywhere (specs/README); render
// with tabular-nums (specs/15 §15.1). No floats in the money path — we divide only
// at the display boundary.

/** Format integer US cents as a USD string, e.g. 83345 -> "$833.45". */
export function formatCents(cents: number | null | undefined): string {
  if (cents == null) return "—";
  const negative = cents < 0;
  const abs = Math.abs(cents);
  const dollars = Math.floor(abs / 100);
  const remainder = abs % 100;
  const formatted = `$${dollars.toLocaleString("en-US")}.${remainder
    .toString()
    .padStart(2, "0")}`;
  return negative ? `-${formatted}` : formatted;
}
