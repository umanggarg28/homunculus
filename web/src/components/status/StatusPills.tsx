import { motion } from "framer-motion";
import type { ServiceName, StatusMap } from "@/lib/types";
import { formatAge } from "@/lib/format";
import { Tooltip } from "@/components/ui/Tooltip";

const SERVICE_BLURB: Record<string, string> = {
  heartbeat: "background daemon. Fires every ~60 min and on every due task, then sleeps.",
  telegram:  "messaging frontend. Long-polls Telegram for messages and routes them to the agent.",
  web:       "HTTP server for this dashboard, /api/* endpoints, and the chat SSE stream.",
};
const STATE_BLURB: Record<string, string> = {
  live:    "currently active — events seen in the last few seconds.",
  idle:    "running but no recent events.",
  stale:   "no events for a long time — service may be stopped.",
  unknown: "no status reported yet.",
};

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

  const tip = (
    <>
      <strong>{name}</strong> · {state} · last seen {ageLabel} ago
      <br />
      {SERVICE_BLURB[name] ?? ""}
      <br />
      <span style={{ color: "var(--color-text-muted)" }}>{STATE_BLURB[state] ?? ""}</span>
    </>
  );

  return (
    <Tooltip text={tip} placement="bottom">
      <div
        className="flex items-center gap-1.5 mono-caps text-[var(--color-text-muted)]"
        style={{ cursor: "help" }}
      >
        <motion.span
          className={`inline-block w-[6px] h-[6px] rounded-full bg-current ${color}`}
          animate={state === "live" ? { opacity: [1, 0.35, 1] } : { opacity: 1 }}
          transition={{ duration: 1.8, repeat: state === "live" ? Infinity : 0, ease: "easeInOut" }}
        />
        <span>{name}</span>
      </div>
    </Tooltip>
  );
}
