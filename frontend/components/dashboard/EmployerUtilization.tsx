// EmployerUtilization — one commitment-utilization Meter per employer program
// (posted ÷ committed), from the employerSummaries read model (specs/05 §5.4). The
// ratio and both endpoints are computed from integer cents and shown in the value
// label so the reading is never length/color alone (specs/15 §15.1). Display-only.

import Card from "@/components/Card";
import Meter from "@/components/charts/Meter";
import { formatCents } from "@/lib/format";
import type { EmployerSummary } from "@/lib/types";
import type { WithId } from "@/lib/readModels";
import { ratioPercent } from "@/components/dashboard/data";
import { RowsSkeleton, SectionError } from "@/components/dashboard/ui";

export interface EmployerUtilizationProps {
  employers: WithId<EmployerSummary>[];
  loading: boolean;
  error: Error | null;
  empty: boolean;
}

export function EmployerUtilization({
  employers,
  loading,
  error,
  empty,
}: EmployerUtilizationProps) {
  return (
    <Card title="Employer commitment utilization" meta="posted / committed">
      <SectionError error={error} context="employer utilization" />
      {error ? null : loading && empty ? (
        <RowsSkeleton rows={4} />
      ) : empty ? (
        <p className="py-6 text-center text-sm text-ink-3">No employer programs.</p>
      ) : (
        <div className="space-y-4">
          {employers.map((e) => {
            const pct = ratioPercent(e.amountPaidCents, e.totalCommitmentCents);
            return (
              <Meter
                key={e.id}
                label={e.employerName}
                value={e.amountPaidCents}
                max={e.totalCommitmentCents}
                token="accent"
                valueLabel={
                  <>
                    {formatCents(e.amountPaidCents)} / {formatCents(e.totalCommitmentCents)}
                    {" · "}
                    {pct == null ? "—" : `${Math.round(pct)}%`}
                  </>
                }
              />
            );
          })}
        </div>
      )}
    </Card>
  );
}

export default EmployerUtilization;
