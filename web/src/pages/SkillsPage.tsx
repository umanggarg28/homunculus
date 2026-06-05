import { useEffect, useMemo, useState, useRef } from "react";
import type { ReactNode } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { BrutalistEmpty } from "@/components/ui/BrutalistEmpty";
import { SkillsHero } from "@/components/ui/HeroBand";
import { LoadingPanel } from "@/components/ui/LoadingPanel";
import { AutonomyConsole } from "@/components/tools/AutonomyConsole";
import type { AgentBudgetStats, AgentControls, AgentReplayTurn, Skill } from "@/lib/types";

type ToolsTab = "console" | "catalog";

export function SkillsPage() {
  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [controls, setControls] = useState<AgentControls | null>(null);
  const [replay, setReplay] = useState<AgentReplayTurn[]>([]);
  const [budgetStats, setBudgetStats] = useState<AgentBudgetStats | null>(null);
  // Console = autonomy controls + recent turns (tune the agent).
  // Catalog = active + available tool list (see what the agent has done).
  // Two distinct mental models — splitting halves the page height.
  const [tab, setTab] = useState<ToolsTab>("console");

  useEffect(() => {
    api.skillsList().then(setSkills).catch(() => setSkills([]));
    api.agentControls().then(setControls).catch(() => undefined);
    api.agentReplay(8).then(setReplay).catch(() => setReplay([]));
    api.statsToday().then(setBudgetStats).catch(() => undefined);
    const id = setInterval(() => {
      api.skillsList().then(setSkills).catch(() => undefined);
      api.agentReplay(8).then(setReplay).catch(() => undefined);
      api.statsToday().then(setBudgetStats).catch(() => undefined);
    }, 30_000);
    return () => clearInterval(id);
  }, []);

  const { needsAttention, used, unused, totalCalls, overallSuccess } = useMemo(() => {
    const list = skills ?? [];
    // V.4 "Needs attention" — only the system's own broken-right-now
    // signal: `consecutive_failures > 0`. The counter is incremented
    // by record_failure and reset to 0 by complete_task, so it
    // self-clears the instant the next call succeeds. No magic
    // thresholds, no failure-ratio heuristics — historical-imperfection
    // is not the same as currently-broken, and tools like read_file
    // legitimately fail (missing path) without being broken.
    const needsAttention = list
      .filter((s) => (s.consecutive_failures ?? 0) > 0)
      .sort((a, b) => {
        // Highest signal first: consecutive failures, then failure ratio
        const aCons = a.consecutive_failures ?? 0;
        const bCons = b.consecutive_failures ?? 0;
        if (aCons !== bCons) return bCons - aCons;
        const aRatio = a.call_count > 0 ? a.failure_count / a.call_count : 0;
        const bRatio = b.call_count > 0 ? b.failure_count / b.call_count : 0;
        return bRatio - aRatio;
      });
    const attentionNames = new Set(needsAttention.map((s) => s.name));
    const used = list
      .filter((s) => s.call_count > 0 && !attentionNames.has(s.name))
      .sort((a, b) => b.call_count - a.call_count);
    const unused = list.filter((s) => s.call_count === 0 && !attentionNames.has(s.name));
    const totalCalls = list.reduce((sum, s) => sum + s.call_count, 0);
    const totalSuccess = list.reduce((sum, s) => sum + s.success_count, 0);
    const totalResults = list.reduce(
      (sum, s) => sum + s.success_count + s.failure_count,
      0,
    );
    return {
      needsAttention,
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

      {/* Tabs — single click-target row. Console (autonomy + recent
          turns) vs Catalog (tool list). Hero stays above both. */}
      <ToolsTabs current={tab} onChange={setTab} catalogCount={skills?.length ?? null} />

      {tab === "console" && skills && controls && (
        <AutonomyConsole
          controls={controls}
          skills={skills}
          replay={replay}
          budgetStats={budgetStats}
          onControlsChange={setControls}
          onReplayChange={setReplay}
        />
      )}

      {tab === "catalog" && (
        skills === null ? (
          <LoadingPanel title="probe tool registry" detail="loading permissions and replay data" />
        ) : skills.length === 0 ? (
          <BrutalistEmpty
            header="NO TOOLS REGISTERED"
            body={<>this is unexpected — the agent should always mount at least the core tools (memory, python_exec, web_fetch). check <code style={{ color: "var(--color-text)" }}>tools/__init__.py</code> on the backend.</>}
          />
        ) : (
          <div className="grid gap-8">
            {needsAttention.length > 0 && (
              <ToolSection
                title="needs attention"
                subtitle="tools with recent failures or consecutive errors — check the skill_*.md for any auto-appended 'Watch out:' notes from T1.2"
                count={needsAttention.length}
                tone="warn"
              >
                {needsAttention.map((s) => <SkillRow key={s.name} skill={s} maxCalls={Math.max(1, needsAttention[0]?.call_count ?? 1)} />)}
              </ToolSection>
            )}
            {used.length > 0 && (
              <ToolSection
                title="active tools"
                subtitle="tools the agent actually used; sorted by call volume"
                count={used.length}
              >
                {used.map((s) => <SkillRow key={s.name} skill={s} maxCalls={used[0]?.call_count ?? 1} />)}
              </ToolSection>
            )}
            {unused.length > 0 && (
              <ToolSection
                title="available tools"
                subtitle="mounted capabilities not yet exercised in this workspace"
                count={unused.length}
                quiet
              >
                {unused.map((s) => <SkillRow key={s.name} skill={s} maxCalls={used[0]?.call_count ?? 1} />)}
              </ToolSection>
            )}
          </div>
        )
      )}
    </PageShell>
  );
}

function ToolsTabs({
  current,
  onChange,
  catalogCount,
}: {
  current: ToolsTab;
  onChange: (t: ToolsTab) => void;
  catalogCount: number | null;
}) {
  const tabs: { id: ToolsTab; label: string; hint: string }[] = [
    { id: "console", label: "Console", hint: "autonomy · recent turns" },
    { id: "catalog", label: "Catalog", hint: catalogCount !== null ? `${catalogCount} tools` : "loading" },
  ];
  return (
    <div
      className="flex gap-0 mb-6"
      style={{ borderBottom: "1px solid var(--color-border)" }}
    >
      {tabs.map((t) => {
        const active = current === t.id;
        return (
          <button
            key={t.id}
            onClick={() => onChange(t.id)}
            style={{
              padding: "10px 18px",
              background: "transparent",
              border: "none",
              borderBottom: `2px solid ${active ? "var(--color-accent)" : "transparent"}`,
              cursor: "pointer",
              fontFamily: "var(--font-mono)",
              textAlign: "left",
              marginBottom: "-1px",
            }}
          >
            <div
              className="text-[11px] uppercase tracking-[0.18em]"
              style={{ color: active ? "var(--color-accent)" : "var(--color-text-muted)" }}
            >
              {t.label}
            </div>
            <div
              className="text-[9px] uppercase tracking-[0.14em] mt-1"
              style={{ color: "var(--color-text-faint)" }}
            >
              {t.hint}
            </div>
          </button>
        );
      })}
    </div>
  );
}

function ToolSection({
  title,
  subtitle,
  count,
  quiet,
  tone,
  children,
}: {
  title: string;
  subtitle: string;
  count: number;
  quiet?: boolean;
  // "warn" renders the title and count in amber to flag attention-needed
  // sections (V.4 of the capability roadmap — skill failure surfacing).
  tone?: "warn";
  children: ReactNode;
}) {
  const titleColor =
    tone === "warn" ? "var(--color-warning)"
    : quiet ? "var(--color-text-faint)"
    : "var(--color-text-muted)";
  const countColor =
    tone === "warn" ? "var(--color-warning)"
    : quiet ? "var(--color-text-faint)"
    : "var(--color-accent)";
  return (
    <section>
      <div className="flex items-baseline justify-between gap-4 mb-3 px-1">
        <div>
          <div className="text-[10px] uppercase tracking-[0.28em]" style={{ color: titleColor }}>
            ── {title}
          </div>
          <div className="text-[11px] mt-1" style={{ color: "var(--color-text-faint)" }}>
            {subtitle}
          </div>
        </div>
        <div className="text-[10px] uppercase tracking-[0.16em]" style={{ color: countColor }}>
          {count.toString().padStart(2, "0")}
        </div>
      </div>
      <div style={{ borderTop: `1px solid ${tone === "warn" ? "var(--color-warning)" : "var(--color-border)"}` }}>
        {children}
      </div>
    </section>
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
            {(skill.uses != null || skill.consecutive_failures != null) && (
              <div style={{ display: "flex", gap: 16, marginTop: 6, fontSize: 10, letterSpacing: "0.10em", textTransform: "uppercase" }}>
                {skill.uses != null && (
                  <span style={{ color: "var(--color-text-faint)" }}>rated {skill.uses}×</span>
                )}
                {skill.consecutive_failures != null && skill.consecutive_failures > 0 && (
                  <span style={{ color: skill.consecutive_failures >= 3 ? "var(--color-danger)" : "var(--color-amber)" }}>
                    {skill.consecutive_failures} consec. fail{skill.consecutive_failures >= 3 ? " ⚠" : ""}
                  </span>
                )}
                {skill.consecutive_failures === 0 && skill.uses != null && skill.uses > 0 && (
                  <span style={{ color: "var(--color-signal)" }}>clean streak</span>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </motion.div>
  );
}
