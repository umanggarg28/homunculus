import { useEffect, useMemo, useState } from "react";
import { motion } from "framer-motion";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { Empty } from "@/components/ui/Empty";
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
    <div className="max-w-[820px] mx-auto px-10 pt-12 pb-20">
      <PageHeader
        title="Skills"
        subtitle={
          skills
            ? `${skills.length} tools registered · ${totalCalls} calls`
              + (overallSuccess !== null ? ` · ${overallSuccess}% success` : "")
            : ""
        }
      />

      {skills === null ? null : skills.length === 0 ? (
        <Empty>No tools registered. (Unexpected — check tools/__init__.py.)</Empty>
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
    </div>
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
  const rateColor =
    rate === null ? "var(--color-text-muted)" :
    rate >= 95 ? "var(--color-warning)" :
    rate >= 75 ? "var(--color-text-dim)" :
                 "var(--color-accent)";

  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25 }}
      className="py-4 px-1"
      style={{ borderBottom: "1px solid var(--color-border)" }}
    >
      <div className="flex items-baseline justify-between gap-4">
        <code
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: 14.5,
            color: "var(--color-text)",
          }}
        >
          {skill.name}
        </code>
        <div className="flex items-baseline gap-3 text-[12.5px]" style={{ color: "var(--color-text-muted)" }}>
          {skill.call_count > 0 && (
            <>
              <span>
                {skill.call_count} call{skill.call_count === 1 ? "" : "s"}
              </span>
              {rate !== null && (
                <span style={{ color: rateColor }}>{rate}% success</span>
              )}
              {skill.last_used && (
                <span>last {formatAge(skill.last_used)}</span>
              )}
            </>
          )}
        </div>
      </div>
      <div
        className="mt-1.5 text-[14.5px] leading-relaxed"
        style={{ fontFamily: "var(--font-sans)", color: "var(--color-text-dim)" }}
      >
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
