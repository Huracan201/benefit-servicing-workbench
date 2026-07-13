// RecentActivity — the "Recent servicing activity" Timeline (specs/04 §4.9). Reads the
// bounded global servicingEvents tail (newest first). The rail color per item is keyed
// to eventType through the shared statusMeta, and the mapped label is always shown, so
// color never stands alone (specs/15 §15.1). Timestamps are pre-formatted in
// SYSTEM_TIMEZONE. Append-only audit stream — display-only.

import Card from "@/components/Card";
import Timeline, { type TimelineItem } from "@/components/Timeline";
import type { ServicingEvent } from "@/lib/types";
import type { WithId } from "@/lib/readModels";
import { eventDetail, formatEventTimestamp } from "@/components/dashboard/data";
import { RowsSkeleton, SectionError } from "@/components/dashboard/ui";

export interface RecentActivityProps {
  events: WithId<ServicingEvent>[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
}

export function RecentActivity({
  events,
  loading,
  error,
  empty,
}: RecentActivityProps) {
  const items: TimelineItem[] = events.map((ev) => ({
    id: ev.id,
    eventType: ev.eventType,
    detail: eventDetail(ev.metadata),
    actor: ev.actorType === "SYSTEM" ? "System" : ev.actorName || "System",
    timestamp: formatEventTimestamp(ev.createdAt),
  }));

  return (
    <Card title="Recent servicing activity" meta="live timeline">
      <SectionError error={error} context="the activity feed" />
      {error ? null : loading && empty ? (
        <RowsSkeleton rows={4} />
      ) : (
        <Timeline items={items} emptyMessage="No recent activity." />
      )}
    </Card>
  );
}

export default RecentActivity;
