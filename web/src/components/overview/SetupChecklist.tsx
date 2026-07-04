import { useEffect, useState } from "react";
import { API_BASE, authHeaders } from "@/lib/api";

interface SetupStatus {
  telegram_configured: boolean;
  location_set: boolean;
  google_connected: boolean;
  tasks_exist: boolean;
  memory_seeded: boolean;
  complete: boolean;
}

const STEPS: Array<{ key: keyof SetupStatus; label: string; how: string }> = [
  { key: "telegram_configured", label: "telegram channel", how: "set TELEGRAM_BOT_TOKEN in .env" },
  { key: "location_set", label: "home location", how: "allow geolocation once, or set a city in settings" },
  { key: "tasks_exist", label: "first task", how: "tasks → + new task, or just ask in chat" },
  { key: "memory_seeded", label: "first memory", how: "tell it something about you in chat" },
  { key: "google_connected", label: "calendar + email (read-only)", how: "scripts/google_auth.py — see .env.example" },
];

/** First-run checklist — shown only while the install is incomplete.
 *  Reads real state from /api/setup/status (env, files, stores); once
 *  everything is wired it disappears for good. The empty dashboard used
 *  to greet a fresh install with dashes and no direction.
 */
export function SetupChecklist() {
  const [status, setStatus] = useState<SetupStatus | null>(null);

  useEffect(() => {
    fetch(`${API_BASE}/setup/status`, { headers: authHeaders() })
      .then((r) => (r.ok ? r.json() : null))
      .then(setStatus)
      .catch(() => undefined);
  }, []);

  if (!status || status.complete) return null;
  const done = STEPS.filter((s) => status[s.key]).length;

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mt-6" style={{ fontFamily: "var(--font-mono)" }}>
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── setup · bring the unit online</span>
        <span style={{ color: "var(--color-accent)", fontVariantNumeric: "tabular-nums" }}>
          {done}/{STEPS.length}
        </span>
      </div>
      {STEPS.map((s) => {
        const ok = status[s.key];
        return (
          <div
            key={s.key}
            className="px-4 py-2.5 flex items-baseline gap-3"
            style={{ borderTop: "1px solid var(--color-border)" }}
          >
            <span style={{ color: ok ? "var(--color-accent)" : "var(--color-text-faint)", width: 14 }}>
              {ok ? "▣" : "▢"}
            </span>
            <span
              className="brut-label"
              style={{
                color: ok ? "var(--color-text-muted)" : "var(--color-text)",
                textDecoration: ok ? "line-through" : "none",
                textDecorationColor: "var(--color-text-faint)",
              }}
            >
              {s.label}
            </span>
            {!ok && (
              <span className="brut-meta" style={{ color: "var(--color-text-muted)", marginLeft: "auto", textAlign: "right" }}>
                {s.how}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
