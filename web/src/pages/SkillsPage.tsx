import { useEffect, useMemo, useState, useRef } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { BrutalistEmpty } from "@/components/ui/BrutalistEmpty";
import { SkillsHero } from "@/components/ui/HeroBand";
import type { Skill } from "@/lib/types";

export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[] | null>(null);

  useEffect(() => {
    api.skillsList().then(setSkills).catch(() => setSkills([]));
    const id = setInterval(() => {
      api.skillsList().then(setSkills).catch(() => undefined);
    }, 30_000);
    return () => clearInterval(id);
  }, []);

  const { used, unused, totalCalls, overallSuccess } = useMemo(() => {
    const list = skills ?? [];
    const used = list
      .filter((s) => s.call_count > 0)
      .sort((a, b) => b.call_count - a.call_count);
    const unused = list.filter((s) => s.call_count === 0);
    const totalCalls = list.reduce((sum, s) => sum + s.call_count, 0);
    const totalSuccess = list.reduce((sum, s) => sum + s.success_count, 0);
    const totalResults = list.reduce(
      (sum, s) => sum + s.success_count + s.failure_count,
      0,
    );
    return {
      used,
      unused,
      totalCalls,
      overallSuccess: totalResults > 0
        ? Math.round((totalSuccess / totalResults) * 100)
        : null,
    };
  }, [skills]);

  return (
    <PageShell>
      <PageHeader
        title="Tools"
        subtitle={
          skills
            ? `${skills.length} tools registered · ${totalCalls} calls`
              + (overallSuccess !== null ? ` · ${overallSuccess}% success` : "")
            : ""
        }
      />

      {skills && skills.length > 0 && <SkillsHero skills={skills} />}

      {skills === null ? null : skills.length === 0 ? (
        <BrutalistEmpty
          header="NO TOOLS REGISTERED"
          body={<>this is unexpected — the agent should always mount at least the core tools (memory, python_exec, web_fetch). check <code style={{ color: "var(--color-text)" }}>tools/__init__.py</code> on the backend.</>}
        />
      ) : (
        <div style={{ borderTop: "1px solid var(--color-border)" }}>
          {[...used, ...unused].map((s) => <SkillRow key={s.name} skill={s} maxCalls={used[0]?.call_count ?? 1} />)}
        </div>
      )}
    </PageShell>
  );
}

function SkillRow({ skill, maxCalls }: { skill: Skill; maxCalls: number }) {
  const [open, setOpen] = useState(false);
  const descRef = useRef<HTMLDivElement>(null);
  const hasFailures = skill.failure_count > 0;
  const barColor = skill.call_count === 0
    ? "var(--color-border)"
    : hasFailures
      ? "var(--color-amber)"
      : "var(--color-accent)";
  const barGlow = skill.call_count === 0
    ? "none"
    : hasFailures
      ? "0 0 5px rgba(255,176,0,0.4)"
      : "0 0 5px var(--color-accent-glow)";
  const barWidth = skill.call_count > 0 ? `${(skill.call_count / Math.max(1, maxCalls)) * 100}%` : "0%";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.2 }}
      style={{ borderBottom: "1px solid var(--color-border)", fontFamily: "var(--font-mono)" }}
    >
      <style>{`
        .skill-row-grid {
          display: grid;
          grid-template-columns: 16px 180px minmax(120px, 1fr) 60px;
          gap: 12px;
          align-items: center;
        }
        @media (max-width: 720px) {
          .skill-row-grid {
            grid-template-columns: 16px minmax(0, 1fr) 52px;
          }
          .skill-row-bar {
            grid-column: 2 / 4;
          }
          .skill-row-desc {
            padding-left: 28px !important;
          }
        }
      `}</style>
      <div
        className="px-4 py-3 cursor-pointer"
        onClick={() => setOpen((v) => !v)}
        onMouseEnter={(e) => { (e.currentTarget as HTMLDivElement).style.background = "rgba(124,254,0,0.02)"; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLDivElement).style.background = "transparent"; }}
      >
        <div className="skill-row-grid">
          {/* Expand toggle */}
          <span style={{ fontSize: 9, color: "var(--color-text-faint)", userSelect: "none", transition: "transform 0.15s", display: "inline-block", transform: open ? "rotate(90deg)" : "none" }}>▶</span>

          {/* Name */}
          <span style={{ fontSize: 12, color: skill.call_count > 0 ? "var(--color-text)" : "var(--color-text-muted)", letterSpacing: "0.02em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            {skill.name}
          </span>

          {/* Bar */}
          <div className="skill-row-bar" style={{ position: "relative", height: 6, background: "var(--color-surface-2)", border: "1px solid var(--color-border)" }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: barWidth, background: barColor, boxShadow: barGlow, transition: "width 0.4s ease" }} />
          </div>

          {/* Count + error rate */}
          <div style={{ textAlign: "right", fontVariantNumeric: "tabular-nums" }}>
            {skill.call_count > 0 ? (
              <div style={{ fontSize: 11, color: barColor }}>
                {skill.call_count.toString().padStart(2, "0")}
              </div>
            ) : (
              <div style={{ fontSize: 11, color: "var(--color-text-faint)" }}>—</div>
            )}
            {hasFailures && (() => {
              const total = skill.success_count + skill.failure_count;
              const errPct = total > 0 ? Math.round((skill.failure_count / total) * 100) : 0;
              return (
                <div style={{ fontSize: 9, color: "var(--color-amber)", letterSpacing: "0.06em" }}>
                  ▴{errPct}%
                </div>
              );
            })()}</div>
        </div>
      </div>

      {/* Expandable description + stats */}
      <div
        ref={descRef}
        style={{
          overflow: "hidden",
          maxHeight: open ? 160 : 0,
          transition: "max-height 0.2s ease",
        }}
      >
        <div className="skill-row-desc px-4 pb-3" style={{ paddingLeft: 44 }}>
          <div style={{ borderLeft: "2px solid var(--color-border)", paddingLeft: 12 }}>
            <div style={{ fontSize: 11, color: "var(--color-text-dim)", letterSpacing: "0.02em", lineHeight: 1.6 }}>
              {skill.description || <em style={{ color: "var(--color-text-faint)" }}>(no description)</em>}
            </div>
            {skill.call_count > 0 && (
              <div style={{ display: "flex", gap: 16, marginTop: 8, fontSize: 10, letterSpacing: "0.10em", textTransform: "uppercase" }}>
                <span style={{ color: "var(--color-accent)" }}>{skill.success_count} ok</span>
                {skill.failure_count > 0 && (
                  <span style={{ color: "var(--color-danger)" }}>{skill.failure_count} failed</span>
                )}
                {skill.failure_count > 0 && (() => {
                  const total = skill.success_count + skill.failure_count;
                  const rate = total > 0 ? Math.round((skill.failure_count / total) * 100) : 0;
                  return <span style={{ color: "var(--color-amber)" }}>{rate}% error rate</span>;
                })()}
                {skill.last_used && (
                  <span style={{ color: "var(--color-text-faint)" }}>
                    last: {new Date(skill.last_used).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
