import type { CSSProperties, ReactNode } from "react";
import type { Task, MemoryEntry, Skill, LogFile } from "@/lib/types";
import { parseTaskWallClock } from "@/lib/api";
import { DataTip } from "@/components/ui/DataTip";

/** Shared shell for every page's hero stat band. Slot-based: a giant
 *  glowing numeral on the left, label below it, a flexible "trail"
 *  (sparkline / ridgeline / bars) on the right. Hard borders, no
 *  rounding. Matches /overview's typographic anchor weight. */
function HeroBandShell({
  meta,
  bigNumber,
  bigUnit,
  bigLabel,
  numberColor,
  trail,
  rightSlot,
}: {
  meta: string;
  bigNumber: ReactNode;
  bigUnit?: string;
  bigLabel: string;
  numberColor?: string;
  trail?: ReactNode;
  rightSlot?: ReactNode;
}) {
  const numColor = numberColor ?? "var(--color-accent)";
  return (
    <div
      className="hero-band instrument-panel"
      style={{
        position: "relative",
        padding: "28px 32px 24px",
        marginBottom: 32,
        overflow: "hidden",
      }}
    >
      <style>{`
        .hero-band-grid {
          display: grid;
          grid-template-columns: auto minmax(180px, 1fr) auto;
          gap: 32px;
          align-items: end;
        }
        @media (max-width: 860px) {
          .hero-band {
            padding: 22px 22px 20px !important;
          }
          .hero-band-grid {
            grid-template-columns: 1fr;
            gap: 18px;
            align-items: start;
          }
          .hero-band-right {
            text-align: left !important;
          }
        }
        @media (max-width: 520px) {
          .hero-band {
            padding: 18px 16px !important;
            margin-bottom: 22px !important;
          }
          .hero-band-number {
            font-size: clamp(42px, 17vw, 72px) !important;
          }
          .hero-band-stat {
            flex-wrap: wrap;
            row-gap: 4px;
          }
          .hero-band-unit {
            font-size: 9px !important;
            letter-spacing: 0.12em !important;
          }
        }
      `}</style>
      <div
        className="brut-meta"
        style={{ color: "var(--color-text-muted)", marginBottom: 12 }}
      >
        ── {meta}
      </div>

      <div
        className="hero-band-grid"
      >
        <div className="hero-band-stat" style={{ display: "flex", alignItems: "baseline", gap: 8, minWidth: 0 }}>
          <span
            className="hero-band-number brut-display"
            style={{
              color: numColor,
              textShadow: `0 0 18px ${numColor}, 0 0 4px ${numColor}`,
              fontSize: "clamp(56px, 8vw, 96px)",
              lineHeight: 0.85,
            }}
          >
            {bigNumber}
          </span>
          {bigUnit && (
            <span
              className="hero-band-unit brut-label"
              style={{ color: "var(--color-text-muted)", fontSize: 11 }}
            >
              {bigUnit}
            </span>
          )}
        </div>

        <div style={{ minWidth: 0 }}>{trail}</div>

        <div className="hero-band-right" style={{ textAlign: "right" }}>{rightSlot}</div>
      </div>

      <div
        className="brut-label"
        style={{ color: "var(--color-text-dim)", marginTop: 10, fontSize: 10 }}
      >
        {bigLabel}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Tasks hero — countdown to next fire + total + recent firings spark
// ────────────────────────────────────────────────────────────────────
export function TasksHero({ tasks, now }: { tasks: Task[]; now: number }) {
  const active = tasks.filter((t) => t.status === "active");

  const upcoming = active
    .map((t) => ({ t, due: t.due_at ? parseTaskWallClock(t.due_at) : null }))
    .filter((x) => x.due !== null && (x.due as number) > now)
    .sort((a, b) => (a.due as number) - (b.due as number));

  let bigNumber: ReactNode = "—";
  let bigUnit = "";
  let label = active.length === 0 ? "no active tasks queued" : "no scheduled fire";
  let numberColor: string | undefined;

  if (upcoming.length > 0) {
    const ms = (upcoming[0].due as number) - now;
    const totalSec = Math.max(0, Math.floor(ms / 1000));
    const days = Math.floor(totalSec / 86400);
    const hours = Math.floor((totalSec % 86400) / 3600);
    const mins = Math.floor((totalSec % 3600) / 60);
    const secs = totalSec % 60;
    if (days > 0) {
      bigNumber = `${days}d ${hours.toString().padStart(2, "0")}h`;
      bigUnit = "UNTIL";
    } else {
      bigNumber = `${hours.toString().padStart(2, "0")}:${mins
        .toString()
        .padStart(2, "0")}:${secs.toString().padStart(2, "0")}`;
      bigUnit = "UNTIL";
    }
    // Fire is imminent → warm to amber under 60s.
    if (totalSec < 60) numberColor = "var(--color-warning)";
    const title = upcoming[0].t.title.length > 64
      ? upcoming[0].t.title.slice(0, 63) + "…"
      : upcoming[0].t.title;
    label = `NEXT · ${title}`;
  }

  // Recent runs sparkline (last 30 runs across all tasks)
  const runs = active
    .flatMap((t) => (t.last_runs ?? []).map((r) => ({ ts: new Date(r.ts).getTime(), ok: r.status === "success" })))
    .sort((a, b) => a.ts - b.ts)
    .slice(-30);

  return (
    <HeroBandShell
      meta={`task queue · ${active.length} active`}
      bigNumber={bigNumber}
      bigUnit={bigUnit}
      bigLabel={label}
      numberColor={numberColor}
      trail={runs.length > 0 ? <RunStrip runs={runs} /> : <FaintNote text="no runs yet — schedule one below" />}
      rightSlot={
        <div className="brut-meta" style={{ color: "var(--color-text-faint)", textAlign: "right" }}>
          <div>{active.length.toString().padStart(2, "0")} ACTIVE</div>
          <div style={{ marginTop: 4 }}>{upcoming.length.toString().padStart(2, "0")} SCHEDULED</div>
        </div>
      }
    />
  );
}

function RunStrip({ runs }: { runs: { ts: number; ok: boolean }[] }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="brut-meta" style={{ color: "var(--color-text-muted)" }}>
        ── last {runs.length} fires
      </div>
      <div style={{ display: "flex", gap: 3, height: 28, alignItems: "flex-end" }}>
        {runs.map((r, i) => (
          <DataTip key={i} tip={new Date(r.ts).toLocaleString()}>
            <span
              style={{
                display: "inline-block",
                width: 6,
                height: r.ok ? 28 : 15,
                background: r.ok ? "var(--color-accent)" : "var(--color-danger)",
                boxShadow: r.ok ? "0 0 6px var(--color-accent-glow)" : "none",
                opacity: 0.5 + (i / runs.length) * 0.5,
              }}
            />
          </DataTip>
        ))}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Memory hero — entry count + type breakdown + write-rate ridgeline
// ────────────────────────────────────────────────────────────────────
export function MemoryHero({ entries }: { entries: MemoryEntry[] }) {
  const byType: Record<string, number> = {};
  for (const e of entries) byType[e.type] = (byType[e.type] ?? 0) + 1;

  const DAY = 86400 * 1000;
  const now = Date.now();
  const buckets = new Array(14).fill(0);
  for (const e of entries) {
    const ageDays = Math.floor((now - e.mtime * 1000) / DAY);
    if (ageDays >= 0 && ageDays < 14) buckets[13 - ageDays]++;
  }
  const newest = entries.reduce<MemoryEntry | null>(
    (acc, e) => (!acc || e.mtime > acc.mtime ? e : acc),
    null,
  );

  return (
    <HeroBandShell
      meta="agent recollection"
      bigNumber={entries.length.toString().padStart(3, "0")}
      bigUnit="ENTRIES"
      bigLabel={
        newest
          ? `NEWEST · ${newest.name.toUpperCase()}`
          : "no memories written yet"
      }
      trail={<MemoryHeatmap buckets={buckets} />}
      rightSlot={
        <div className="brut-meta" style={{ color: "var(--color-text-faint)", textAlign: "right", lineHeight: 1.6 }}>
          {(["user", "feedback", "project", "skill", "reference"] as const).map((t) => {
            const n = byType[t] ?? 0;
            if (n === 0) return null;
            return (
              <div key={t}>
                {n.toString().padStart(2, "0")} {t.toUpperCase()}
              </div>
            );
          })}
        </div>
      }
    />
  );
}

function MemoryHeatmap({ buckets }: { buckets: number[] }) {
  const max = Math.max(1, ...buckets);
  const dayLabels = buckets.map((_, i) => {
    const d = new Date(Date.now() - (13 - i) * 86400 * 1000);
    return `${d.toLocaleDateString("en", { month: "short", day: "numeric" })}`;
  });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="brut-meta" style={{ color: "var(--color-text-muted)" }}>
        ── last 14d · write density
      </div>
      <div style={{ display: "flex", gap: 4, alignItems: "center" }}>
        {buckets.map((v, i) => {
          const intensity = v > 0 ? 0.2 + 0.8 * (v / max) : 0;
          return (
            <DataTip key={i} tip={`${dayLabels[i]}: ${v} write${v !== 1 ? "s" : ""}`}>
            <div
              style={{
                width: 18,
                height: 18,
                background: v > 0
                  ? `rgba(124,254,0,${intensity})`
                  : "var(--color-border)",
                border: `1px solid ${v > 0 ? "rgba(124,254,0,0.3)" : "var(--color-border)"}`,
                boxShadow: v > 0 ? `0 0 6px rgba(124,254,0,${intensity * 0.5})` : "none",
                flexShrink: 0,
              }}
            />
            </DataTip>
          );
        })}
      </div>
    </div>
  );
}

function Ridgeline({ buckets, label }: { buckets: number[]; label: string }) {
  const max = Math.max(1, ...buckets);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div className="brut-meta" style={{ color: "var(--color-text-muted)" }}>
        ── {label}
      </div>
      <div style={{ display: "flex", gap: 4, height: 32, alignItems: "flex-end" }}>
        {buckets.map((v, i) => (
          <span
            key={i}
            style={{
              flex: 1,
              minWidth: 4,
              height: v > 0 ? `${(v / max) * 100}%` : 2,
              background: v > 0 ? "var(--color-accent)" : "var(--color-border)",
              boxShadow: v > 0 ? "0 0 4px var(--color-accent-glow)" : "none",
              opacity: v > 0 ? 0.7 + 0.3 * (v / max) : 1,
            }}
          />
        ))}
      </div>
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Skills hero — total calls + success rate + top-6 frequency bars
// ────────────────────────────────────────────────────────────────────
export function SkillsHero({ skills }: { skills: Skill[] }) {
  const used = skills.filter((s) => s.call_count > 0);
  const totalCalls = skills.reduce((s, x) => s + x.call_count, 0);
  const totalOk = skills.reduce((s, x) => s + x.success_count, 0);
  const totalRes = skills.reduce((s, x) => s + x.success_count + x.failure_count, 0);
  const rate = totalRes > 0 ? Math.round((totalOk / totalRes) * 100) : null;

  const top = [...used].sort((a, b) => b.call_count - a.call_count).slice(0, 6);
  const max = Math.max(1, ...top.map((s) => s.call_count));

  return (
    <HeroBandShell
      meta={`tool registry · ${skills.length} mounted`}
      bigNumber={totalCalls.toString().padStart(3, "0")}
      bigUnit="CALLS"
      bigLabel={
        rate !== null
          ? `${rate}% SUCCESS · ${used.length}/${skills.length} TOOLS USED`
          : `${used.length}/${skills.length} TOOLS USED`
      }
      numberColor={rate !== null && rate < 80 ? "var(--color-warning)" : undefined}
      trail={
        top.length > 0 ? <FrequencyBars top={top} max={max} /> : <FaintNote text="no calls yet" />
      }
      rightSlot={
        rate !== null ? (
          <div className="brut-meta" style={{ color: "var(--color-text-faint)", textAlign: "right" }}>
            <div style={{ fontSize: 22, color: rate >= 80 ? "var(--color-accent)" : "var(--color-warning)", letterSpacing: "0", fontWeight: 700 }}>
              {rate}%
            </div>
            <div style={{ marginTop: 2 }}>SUCCESS RATE</div>
          </div>
        ) : null
      }
    />
  );
}

function FrequencyBars({ top, max }: { top: Skill[]; max: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginTop: -8 }}>
      <div className="brut-meta" style={{ color: "var(--color-text-muted)" }}>
        ── top {top.length} by call count
      </div>
      {top.map((s) => (
        <div key={s.name} style={{ display: "grid", gridTemplateColumns: "110px 1fr 28px", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 10, color: "var(--color-text-dim)", fontFamily: "var(--font-mono)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {s.name}
          </span>
          <span style={{ display: "block", height: 6, background: "var(--color-border)", position: "relative" }}>
            <span
              style={{
                position: "absolute",
                left: 0, top: 0, bottom: 0,
                width: `${(s.call_count / max) * 100}%`,
                background: "var(--color-accent)",
                boxShadow: "0 0 5px var(--color-accent-glow)",
              }}
            />
          </span>
          <span style={{ fontSize: 10, color: "var(--color-accent)", textAlign: "right", fontFamily: "var(--font-mono)" }}>
            {s.call_count.toString().padStart(2, "0")}
          </span>
        </div>
      ))}
    </div>
  );
}

// ────────────────────────────────────────────────────────────────────
// Logs hero — total transcript count + bytes-per-day ridgeline
// ────────────────────────────────────────────────────────────────────
export function LogsHero({ logs }: { logs: LogFile[] }) {
  const totalKb = logs.reduce((s, l) => s + l.size_kb, 0);

  // Build last 30 days ridgeline by date
  const DAY = 86400 * 1000;
  const now = Date.now();
  const buckets = new Array(30).fill(0);
  for (const l of logs) {
    const ageDays = Math.floor((now - l.mtime * 1000) / DAY);
    if (ageDays >= 0 && ageDays < 30) {
      buckets[29 - ageDays] += l.size_kb;
    }
  }
  const newest = logs[0]?.date ?? null;

  return (
    <HeroBandShell
      meta={`transcript archive · ${logs.length} days`}
      bigNumber={totalKb >= 1000 ? `${(totalKb / 1000).toFixed(1)}` : totalKb.toFixed(1)}
      bigUnit={totalKb >= 1000 ? "MB" : "KB"}
      bigLabel={newest ? `LATEST · ${newest.toUpperCase()}` : "no transcripts yet"}
      trail={<Ridgeline buckets={buckets} label="last 30d · bytes/day" />}
      rightSlot={
        <div className="brut-meta" style={{ color: "var(--color-text-faint)", textAlign: "right" }}>
          <div>{logs.length.toString().padStart(3, "0")} DAYS</div>
          <div style={{ marginTop: 4 }}>{Math.max(...logs.map((l) => l.size_kb), 0).toFixed(1)}KB PEAK</div>
        </div>
      }
    />
  );
}

function FaintNote({ text, style }: { text: string; style?: CSSProperties }) {
  return (
    <div className="brut-label" style={{ color: "var(--color-text-faint)", ...style }}>
      {text}
    </div>
  );
}
