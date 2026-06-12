import { useEffect, useState } from "react";
import { api } from "@/lib/api";

/** Transmissions — what the agent actually delivered to the user's
 * phone, newest first, grouped by day. Lives on OVERVIEW: it's
 * operational evidence, and putting it on the landing page turned the
 * hero into a second dashboard.
 *
 * Labels stay monochrome (phosphor discipline: hue is reserved for
 * state, and red is the only state worth shouting about); the newest
 * transmission glows accent.
 */

interface Tx { ts: number; text: string }

export function TransmissionsFeed() {
  const [items, setItems] = useState<Tx[]>([]);

  useEffect(() => {
    let cancelled = false;
    const load = () =>
      api.notificationsRecent(8)
        .then((v) => { if (!cancelled) setItems([...v].reverse()); })
        .catch(() => undefined);
    load();
    const t = setInterval(load, 60_000);
    return () => { cancelled = true; clearInterval(t); };
  }, []);

  if (items.length === 0) return null;

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mt-6">
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── transmissions · what reached your phone</span>
        <span style={{ color: "var(--color-text-faint)" }}>{items.length} recent</span>
      </div>
      <div style={{ fontFamily: "var(--font-mono)" }}>
        {groupByDay(items).map((group) => (
          <div key={group.label}>
            <div className="brut-label px-4 pt-3 pb-2" style={{ color: "var(--color-text-faint)", letterSpacing: "0.22em" }}>
              ─ {group.label}
            </div>
            {group.items.map((n, i) => {
              const kind = kindOf(n.text);
              const newest = n === items[0];
              return (
                <div
                  key={`${n.ts}-${i}`}
                  className="hm-interactive-row px-4 py-2.5 flex gap-4 items-baseline"
                  style={{ borderTop: "1px solid var(--color-border)" }}
                >
                  <span
                    className="brut-label"
                    style={{
                      color: kind.alert ? "var(--color-danger)" : "var(--color-text-muted)",
                      border: `1px solid ${kind.alert ? "color-mix(in srgb, var(--color-danger) 50%, transparent)" : "var(--color-border-strong)"}`,
                      padding: "1px 7px",
                      flexShrink: 0,
                      width: 86,
                      textAlign: "center",
                      letterSpacing: "0.14em",
                    }}
                  >
                    {kind.label}
                  </span>
                  <span
                    className="brut-body truncate"
                    style={{
                      color: newest ? "var(--color-accent)" : "var(--color-text)",
                      textShadow: newest ? "0 0 10px var(--color-accent-glow)" : "none",
                      minWidth: 0,
                      flex: 1,
                    }}
                    title={n.text}
                  >
                    {firstLine(n.text)}
                  </span>
                  {/* Fixed sub-columns: HH:MM right-aligned in 5ch, age in
                      8ch — otherwise the time column wobbles per row. */}
                  <span className="brut-label flex gap-3" style={{ color: "var(--color-text-faint)", flexShrink: 0, whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>
                    <span style={{ width: "5ch", textAlign: "right" }}>
                      {new Date(n.ts * 1000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false })}
                    </span>
                    <span style={{ opacity: 0.55, width: "8ch" }}>
                      {formatAgo(Date.now() / 1000 - n.ts)}
                    </span>
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}

/** Classify by content so rows are scannable; the queue doesn't know
 * which task sent a message, so this is text-derived. */
function kindOf(text: string): { label: string; alert: boolean } {
  const t = text.trimStart();
  if (t.startsWith("⚠")) return { label: "alert", alert: true };
  if (/leetcode\.com\/problems\//i.test(t)) return { label: "leetcode", alert: false };
  if (/^reminder|remember to|is scheduled|is now due/i.test(t)) return { label: "reminder", alert: false };
  if (/brief|digest|standup/i.test(t)) return { label: "brief", alert: false };
  return { label: "tx", alert: false };
}

function groupByDay(items: Tx[]): { label: string; items: Tx[] }[] {
  const dayLabel = (ts: number): string => {
    const d = new Date(ts * 1000);
    const today = new Date();
    const yesterday = new Date(today.getTime() - 86_400_000);
    const same = (a: Date, b: Date) => a.toDateString() === b.toDateString();
    if (same(d, today)) return "today";
    if (same(d, yesterday)) return "yesterday";
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric" }).toLowerCase();
  };
  const groups: { label: string; items: Tx[] }[] = [];
  for (const n of items) {
    const label = dayLabel(n.ts);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(n);
    else groups.push({ label, items: [n] });
  }
  return groups;
}

function firstLine(text: string): string {
  const line = text.split("\n").find((l) => l.trim()) ?? "";
  return line.length > 140 ? line.slice(0, 140) + "…" : line;
}

function formatAgo(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}
