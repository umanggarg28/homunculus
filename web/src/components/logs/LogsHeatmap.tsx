import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import type { LogFile } from "@/lib/types";

/** Conversation-density calendar — GitHub contribution graph, phosphor
 *  edition. One cell per day, brightness follows transcript size
 *  (kb written is the closest proxy for how much actually happened),
 *  click opens that day's log. Weeks are columns, newest on the right.
 *
 *  sqrt scaling, same reason as the traces density grid: one huge day
 *  shouldn't flatten every normal day to near-black.
 */

const WEEKS = 16;
const CELL = 13;
const GAP = 3;
const DOW_LABELS: [number, string][] = [[1, "mon"], [3, "wed"], [5, "fri"]];

export function LogsHeatmap({ logs }: { logs: LogFile[] }) {
  const navigate = useNavigate();

  const { days, monthLabels, maxKb } = useMemo(() => {
    const byDate = new Map(logs.map((l) => [l.date, l]));
    const maxKb = Math.max(...logs.map((l) => l.size_kb), 1);

    // End the grid on the current week's Saturday so today is in the
    // last column.
    const today = new Date();
    const end = new Date(today);
    end.setDate(end.getDate() + (6 - end.getDay()));
    const start = new Date(end);
    start.setDate(start.getDate() - WEEKS * 7 + 1);

    const days: { date: string; col: number; row: number; log?: LogFile; future: boolean }[] = [];
    const monthLabels: { col: number; label: string }[] = [];
    let lastMonth = -1;
    for (let i = 0; i < WEEKS * 7; i++) {
      const d = new Date(start);
      d.setDate(start.getDate() + i);
      const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
      const col = Math.floor(i / 7);
      if (d.getDate() <= 7 && d.getMonth() !== lastMonth && d.getDay() === 0) {
        monthLabels.push({ col, label: d.toLocaleDateString("en-US", { month: "short" }).toLowerCase() });
        lastMonth = d.getMonth();
      }
      days.push({ date: iso, col, row: d.getDay(), log: byDate.get(iso), future: d > today });
    }
    return { days, monthLabels, maxKb };
  }, [logs]);

  const gridW = WEEKS * (CELL + GAP);
  const gridH = 7 * (CELL + GAP);
  const labelW = 30;

  return (
    <div className="instrument-panel hm-panel-scan hm-panel-secondary mb-6">
      <div
        className="brut-meta px-4 py-3 flex justify-between"
        style={{ color: "var(--color-text-muted)", borderBottom: "1px solid var(--color-border)" }}
      >
        <span>── density · last {WEEKS} weeks of conversation</span>
        <span style={{ color: "var(--color-text-faint)" }}>{logs.length} transcripts</span>
      </div>
      <div className="px-4 py-3" style={{ overflowX: "auto" }}>
        <svg
          width={labelW + gridW}
          height={gridH + 16}
          style={{ display: "block", fontFamily: "var(--font-mono)" }}
        >
          {monthLabels.map((m) => (
            <text
              key={`${m.col}-${m.label}`}
              x={labelW + m.col * (CELL + GAP)}
              y={9}
              style={{ fontSize: 9, letterSpacing: "0.14em", fill: "var(--color-text-faint)" }}
            >
              {m.label}
            </text>
          ))}
          {DOW_LABELS.map(([row, label]) => (
            <text
              key={label}
              x={0}
              y={16 + row * (CELL + GAP) + CELL - 3}
              style={{ fontSize: 9, letterSpacing: "0.1em", fill: "var(--color-text-faint)" }}
            >
              {label}
            </text>
          ))}
          {days.map((d) => {
            if (d.future) return null;
            const intensity = d.log ? Math.sqrt(d.log.size_kb / maxKb) : 0;
            return (
              <rect
                key={d.date}
                x={labelW + d.col * (CELL + GAP)}
                y={16 + d.row * (CELL + GAP)}
                width={CELL}
                height={CELL}
                fill={
                  d.log
                    ? `color-mix(in srgb, var(--color-accent) ${Math.round(18 + intensity * 82)}%, var(--color-bg))`
                    : "transparent"
                }
                stroke={d.log ? "none" : "var(--color-border)"}
                strokeWidth={d.log ? 0 : 1}
                style={{ cursor: d.log ? "pointer" : "default" }}
                onClick={() => d.log && navigate(`/logs/${d.log.rel}`)}
              >
                <title>{d.log ? `${d.date} · ${d.log.size_kb.toFixed(1)}kb — open transcript` : `${d.date} · silent`}</title>
              </rect>
            );
          })}
        </svg>
      </div>
    </div>
  );
}
