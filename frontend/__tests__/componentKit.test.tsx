import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import StatusPill from "@/components/Pill";
import Button from "@/components/Button";
import {
  eventTypeMeta,
  severityMeta,
  statusMeta,
} from "@/components/statusMeta";

describe("statusMeta", () => {
  it("maps known statuses to a reserved token + label", () => {
    expect(statusMeta("POSTED")).toEqual({ token: "good", label: "Posted" });
    expect(statusMeta("RETRY_PENDING")).toEqual({
      token: "serious",
      label: "Retry pending",
    });
    expect(statusMeta("FAILED").token).toBe("critical");
  });

  it("falls back to neutral + a humanized label for unknown values", () => {
    expect(statusMeta("SOMETHING_NEW")).toEqual({
      token: "neutral",
      label: "Something New",
    });
  });

  it("keys severity off the numeric rank, not the string", () => {
    expect(severityMeta(40)).toEqual({ token: "critical", label: "Critical" });
    expect(severityMeta(30)).toEqual({ token: "serious", label: "High" });
    expect(severityMeta(20).token).toBe("warning");
    expect(severityMeta(10).token).toBe("neutral");
    // Ranks between the canonical steps still resolve sensibly.
    expect(severityMeta(35).label).toBe("High");
  });

  it("maps servicing event types for the timeline", () => {
    expect(eventTypeMeta("PAYMENT_POSTED").token).toBe("good");
    expect(eventTypeMeta("PAYMENT_FAILED").token).toBe("critical");
    expect(eventTypeMeta("MANUAL_NOTE_ADDED").token).toBe("accent");
  });
});

describe("StatusPill", () => {
  it("always renders a text label (never color alone)", () => {
    render(<StatusPill status="SCHEDULED" />);
    expect(screen.getByText("Scheduled")).toBeInTheDocument();
  });

  it("honors an explicit label override", () => {
    render(<StatusPill status="POSTED" label="Paid" />);
    expect(screen.getByText("Paid")).toBeInTheDocument();
  });
});

describe("Button (locked affordance)", () => {
  it("stays focusable but suppresses activation when locked", () => {
    const onClick = vi.fn();
    render(
      <Button locked lockedReason="Requires Servicing Manager" onClick={onClick}>
        Retry
      </Button>,
    );
    const btn = screen.getByRole("button", { name: /Retry/ });
    // Not the real `disabled` attribute (so the tooltip stays reachable)…
    expect(btn).not.toBeDisabled();
    expect(btn).toHaveAttribute("aria-disabled", "true");
    // …but clicks are ignored.
    fireEvent.click(btn);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("fires onClick when enabled", () => {
    const onClick = vi.fn();
    render(<Button onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button", { name: "Go" }));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
