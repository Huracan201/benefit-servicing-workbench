import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import StatusBadge, { toneForStatus } from "@/components/StatusBadge";

describe("StatusBadge", () => {
  it("renders a humanized label for a status value", () => {
    render(<StatusBadge status="RETRY_PENDING" />);
    expect(screen.getByText("Retry Pending")).toBeInTheDocument();
  });

  it("renders an explicit label when provided", () => {
    render(<StatusBadge status="POSTED" label="Paid" />);
    expect(screen.getByText("Paid")).toBeInTheDocument();
  });

  it("exposes an accessible status role with a text alternative (never color alone)", () => {
    render(<StatusBadge status="FAILED" />);
    const badge = screen.getByRole("status");
    expect(badge).toHaveAttribute("aria-label", "Failed");
  });

  it("maps known statuses to reserved tones and falls back to neutral", () => {
    expect(toneForStatus("POSTED")).toBe("success");
    expect(toneForStatus("CRITICAL")).toBe("critical");
    expect(toneForStatus("SOMETHING_UNKNOWN")).toBe("neutral");
  });
});
