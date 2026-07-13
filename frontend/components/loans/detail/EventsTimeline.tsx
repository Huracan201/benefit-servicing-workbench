// Region 7 — EventsTimeline. The per-loan servicing-event feed (specs/04 §4.9) read from
// the SOURCE `loans/{loanId}/events` mirror (newest first) and rendered through the part-1
// Timeline. The event type selects the reserved rail color + a default label; the visible
// label always accompanies the color (specs/15 §15.1). Timestamps render in SYSTEM_TIMEZONE.

import Card from "@/components/Card";
import Skeleton from "@/components/Skeleton";
import Timeline, { type TimelineItem } from "@/components/Timeline";
import type { WithId } from "@/lib/readModels";
import type { ServicingEvent } from "@/lib/types";
import { formatDateTime } from "@/components/loans/detail/time";

export interface EventsTimelineProps {
  events: WithId<ServicingEvent>[];
  loading: boolean;
}

/** Best-effort human detail from an event's free-form metadata; falls back to the entity. */
function eventDetail(event: WithId<ServicingEvent>): string {
  const md = event.metadata ?? {};
  const candidate = md.summary ?? md.description ?? md.detail;
  if (typeof candidate === "string" && candidate.trim() !== "") return candidate;
  return `${event.entityType} · ${event.entityId}`;
}

function actorLabel(event: WithId<ServicingEvent>): string {
  if (event.actorType === "SYSTEM") return "System";
  const name = event.actorName?.trim() || "Unknown";
  return event.actorRole ? `${name} · ${event.actorRole}` : name;
}

export function EventsTimeline({ events, loading }: EventsTimelineProps) {
  const items: TimelineItem[] = events.map((event) => ({
    id: event.id,
    eventType: event.eventType,
    detail: eventDetail(event),
    actor: actorLabel(event),
    timestamp: formatDateTime(event.createdAt),
  }));

  return (
    <Card title="Servicing timeline" meta="most recent activity">
      {/* Persistent live region (mounted regardless of `loading`) so the "Loading" state is
          reliably announced — toggling only its text, matching the Toast pattern. */}
      <span role="status" className="sr-only">
        {loading ? "Loading activity" : ""}
      </span>
      {loading ? (
        <div>
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} aria-hidden="true" className="flex gap-3 pb-4">
              <Skeleton circle className="mt-1 h-2.5 w-2.5 shrink-0" />
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex items-baseline justify-between gap-3">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-14" />
                </div>
                <Skeleton className="h-3.5 w-2/3" />
              </div>
            </div>
          ))}
        </div>
      ) : (
        <Timeline items={items} emptyMessage="No servicing activity yet." />
      )}
    </Card>
  );
}

export default EventsTimeline;
