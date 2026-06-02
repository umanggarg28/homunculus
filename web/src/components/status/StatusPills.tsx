import { motion } from "framer-motion";
import type { ServiceName, StatusMap } from "@/lib/types";
import { formatAge } from "@/lib/format";

const SERVICES: ServiceName[] = ["heartbeat", "telegram", "web"];

/** A row of four pills, one per service. Each pulses subtly when live. */
export function StatusPills({ status }: { status: StatusMap }) {
  return (
    <div className="flex items-center gap-4">
      {SERVICES.map((svc) => {
        const info = status[svc];
        const state = info?.state ?? "unknown";
        return (
          <Pill
            key={svc}
            name={svc}
            state={state}
            ageLabel={formatAge(info?.age_s ?? null)}
          />
        );
      })}
    </div>
  );
}

function Pill({
  name,
  state,
  ageLabel,
}: {
  name: string;
  state: string;
  ageLabel: string;
}) {
  const color =
    state === "live" ? "text-[var(--color-signal)]"
    : state === "idle" ? "text-[var(--color-warning)]"
    : state === "stale" ? "text-[var(--color-danger)]"
    : "text-[var(--color-text-faint)]";

  return (
    <div
      className="flex items-center gap-1.5 mono-caps text-[var(--color-text-muted)]"
      title={`${name}: ${state} (last seen ${ageLabel} ago)`}
    >
      <motion.span
        className={`inline-block w-[6px] h-[6px] rounded-full bg-current ${color}`}
        animate={state === "live" ? { opacity: [1, 0.35, 1] } : { opacity: 1 }}
        transition={{ duration: 1.8, repeat: state === "live" ? Infinity : 0, ease: "easeInOut" }}
      />
      <span>{name}</span>
    </div>
  );
}
