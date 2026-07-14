import { describe, it, expect } from "vitest";
import { formatCents } from "@/lib/format";

describe("formatCents", () => {
  it("renders an em dash for null / undefined", () => {
    expect(formatCents(null)).toBe("—");
    expect(formatCents(undefined)).toBe("—");
  });

  it("formats whole + fractional dollars with a leading $ and zero-padded cents", () => {
    expect(formatCents(0)).toBe("$0.00");
    expect(formatCents(5)).toBe("$0.05");
    expect(formatCents(105)).toBe("$1.05");
    expect(formatCents(83345)).toBe("$833.45");
  });

  it("groups thousands", () => {
    expect(formatCents(100000000)).toBe("$1,000,000.00");
  });

  it("prefixes a minus for negatives (the money path stays integer cents)", () => {
    expect(formatCents(-1)).toBe("-$0.01");
    expect(formatCents(-500)).toBe("-$5.00");
  });
});
