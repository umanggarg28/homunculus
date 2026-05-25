import { useEffect, useMemo, useState } from "react";
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
        <>
          {used.length > 0 && (
            <Section label="Used">
              <div style={{ borderTop: "1px solid var(--color-border)" }}>
                {used.map((s) => <SkillRow key={s.name} skill={s} />)}
              </div>
            </Section>
          )}
          {unused.length > 0 && (
            <Section label="Never used">
              <div style={{ borderTop: "1px solid var(--color-border)" }}>
                {unused.map((s) => <SkillRow key={s.name} skill={s} />)}
              </div>
            </Section>
          )}
        </>
      )}
    </PageShell>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="mb-10">
      <div className="label mb-3">{label}</div>
      {children}
    </div>
  );
}

function SkillRow({ skill }: { skill: Skill }) {
  const total = skill.success_count + skill.failure_count;
  const rate = total > 0 ? Math.round((skill.success_count / total) * 100) : null;
  // High success = phosphor; <80% drops to amber; <50% goes red.
  const rateColor =
    rate === null ? "var(--color-text-muted)" :
    rate >= 80 ? "var(--color-accent)" :
    rate >= 50 ? "var(--color-warning)" :
                 "var(--color-danger)";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="py-[10px] px-1"
      style={{ borderBottom: "1px solid var(--color-border)" }}
    >
      <div className="flex items-baseline justify-between gap-4">
        <code className="brut-body" style={{ color: "var(--color-text)" }}>
          {skill.name}
        </code>
        <div className="flex items-baseline gap-4 brut-label" style={{ color: "var(--color-text-muted)" }}>
          {skill.call_count > 0 && (
            <>
              <span className="brut-num">
                {skill.call_count.toString().padStart(2, "0")} call{skill.call_count === 1 ? "" : "s"}
              </span>
              {rate !== null && (
                <span className="brut-num" style={{ color: rateColor }}>{rate}%</span>
              )}
              {skill.last_used && <span>{formatAge(skill.last_used)}</span>}
            </>
          )}
        </div>
      </div>
      <div className="mt-2 brut-body" style={{ color: "var(--color-text-dim)" }}>
        {skill.description || <em style={{ color: "var(--color-text-muted)" }}>(no description)</em>}
      </div>
    </motion.div>
  );
}

function formatAge(iso: string): string {
  const d = new Date(iso);
  const diffMin = Math.floor((Date.now() - d.getTime()) / 60000);
  if (diffMin < 1) return "just now";
  if (diffMin < 60) return `${diffMin}m ago`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}h ago`;
  const diffD = Math.floor(diffH / 24);
  return `${diffD}d ago`;
}
