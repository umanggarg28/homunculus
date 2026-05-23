import { useEffect, useMemo, useState } from "react";
import { useEventStream } from "@/hooks/useEventStream";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { HeartbeatRibbon } from "@/components/overview/HeartbeatRibbon";
import { StatTile } from "@/components/overview/StatTile";
import { UpNextList } from "@/components/overview/UpNextList";
import { RecentActivity } from "@/components/overview/RecentActivity";
import type { MemoryEntry, Skill } from "@/lib/types";

export function OverviewPage() {
  const { events } = useEventStream(500);
  const [memories, setMemories] = useState<MemoryEntry[]>([]);
  const [skills, setSkills] = useState<Skill[]>([]);

  useEffect(() => {
    api.memoryList().then(setMemories).catch(() => undefined);
    api.skillsList().then(setSkills).catch(() => undefined);
  }, []);

  const stats = useMemo(() => {
    const startOfDay = new Date();
    startOfDay.setHours(0, 0, 0, 0);
    const startOfDayMs = startOfDay.getTime();

    const today = events.filter((e) => new Date(e.ts).getTime() >= startOfDayMs);
    const toolCalls = today.filter((e) => e.event === "tool_call").length;
    const llmCalls = today.filter((e) => e.event === "llm_call").length;
    const failures = today.filter(
      (e) => e.event === "tool_result" && typeof e.result === "string" && e.result.startsWith("ERROR"),
    ).length;
    const replies = today.filter((e) => e.event === "assistant_reply").length;

    return { toolCalls, llmCalls, failures, replies };
  }, [events]);

  const lastEvent = events[events.length - 1];
  const lastEventAge = lastEvent
    ? Math.floor((Date.now() - new Date(lastEvent.ts).getTime()) / 1000)
    : null;

  return (
    <div className="max-w-[1200px] mx-auto px-8 pt-10 pb-16">
      <PageHeader
        title="Overview"
        subtitle={liveStateMessage(lastEventAge)}
      />

      <div className="mb-6">
        <HeartbeatRibbon events={events} hours={24} />
      </div>

      {/* 4-up stat row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-8">
        <StatTile
          label="Actions today"
          value={stats.toolCalls}
          hint="tool calls fired"
          accent="mint"
        />
        <StatTile
          label="Replies today"
          value={stats.replies}
          hint="agent responses"
          accent="indigo"
        />
        <StatTile
          label="Model calls"
          value={stats.llmCalls}
          hint="LLM round-trips today"
          accent="amber"
        />
        <StatTile
          label="Failures"
          value={stats.failures}
          hint={stats.failures === 0 ? "all clean" : "tool errors today"}
          accent={stats.failures === 0 ? "muted" : "mint"}
        />
      </div>

      {/* Two-column body: Up Next + Recent Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <UpNextList />
        <RecentActivity events={events} />
      </div>

      {/* Tertiary: memory + skills snapshot */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-8">
        <StatTile
          label="Memory"
          value={memories.length}
          hint={`entries · ${countBy(memories, "type", "skill")} skills · ${countBy(memories, "type", "project")} projects`}
          accent="muted"
        />
        <StatTile
          label="Skills (tools)"
          value={skills.length}
          hint={`${skills.filter((s) => s.call_count > 0).length} ever called`}
          accent="muted"
        />
      </div>
    </div>
  );
}

function liveStateMessage(lastEventAgeSec: number | null): string {
  if (lastEventAgeSec === null) return "The agent is at rest. No activity yet.";
  if (lastEventAgeSec < 10) return "The agent is acting right now.";
  if (lastEventAgeSec < 60) return `Last activity ${lastEventAgeSec}s ago.`;
  if (lastEventAgeSec < 3600) return `Last activity ${Math.floor(lastEventAgeSec / 60)}m ago.`;
  return `Last activity ${Math.floor(lastEventAgeSec / 3600)}h ago.`;
}

function countBy<T>(items: T[], key: keyof T, value: unknown): number {
  return items.filter((i) => i[key] === value).length;
}
