import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { PageHeader } from "@/components/ui/PageHeader";
import { PageShell } from "@/components/ui/PageShell";
import { LoadingPanel } from "@/components/ui/LoadingPanel";
import { BrutalistEmpty } from "@/components/ui/BrutalistEmpty";
import { Tooltip } from "@/components/ui/Tooltip";
import { EvalScorecardGrid } from "@/components/evals/EvalScorecardGrid";
import type { EvalScorecards } from "@/lib/types";

type EvalView = "cards" | "table";

export function EvalsPage() {
  const [cards, setCards] = useState<EvalScorecards | null>(null);
  const [view, setView] = useState<EvalView>(() => {
    const saved = localStorage.getItem("hm-evals-view");
    return saved === "table" ? "table" : "cards";
  });

  useEffect(() => {
    const load = () => api.evals().then(setCards).catch(() => setCards({}));
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, []);

  const pickView = (v: EvalView) => {
    setView(v);
    localStorage.setItem("hm-evals-view", v);
  };

  const entries = Object.entries(cards ?? {}).sort(([a], [b]) => a.localeCompare(b));

  return (
    <PageShell>
      <PageHeader
        title="Evals"
        subtitle={cards ? `${entries.length} skill${entries.length === 1 ? "" : "s"} scored` : ""}
        actions={<ViewToggle view={view} onPick={pickView} />}
      />

      {cards === null ? (
        <LoadingPanel title="score trajectories" detail="reading run history and event log" />
      ) : entries.length === 0 ? (
        <BrutalistEmpty
          header="NOTHING TO SCORE YET"
          body={<>no skill-linked task has run yet. once a scheduled task (like <code style={{ color: "var(--color-text)" }}>quiz-coach</code>) completes at least once, its trajectory shows up here.</>}
        />
      ) : (
        <EvalScorecardGrid cards={entries} view={view} />
      )}
    </PageShell>
  );
}

function ViewToggle({ view, onPick }: { view: EvalView; onPick: (v: EvalView) => void }) {
  const OPTIONS: { id: EvalView; label: string; hint: string }[] = [
    { id: "cards", label: "cards", hint: "One scorecard per skill." },
    { id: "table", label: "table", hint: "Same data, dense rows." },
  ];
  return (
    <div
      className="flex items-baseline gap-1 text-[10px] uppercase tracking-[0.14em] select-none"
      style={{ fontFamily: "var(--font-mono)" }}
    >
      <span style={{ color: "var(--color-text-faint)", marginRight: 4 }}>view</span>
      {OPTIONS.map((o) => (
        <Tooltip key={o.id} text={o.hint} placement="bottom">
          <button
            onClick={() => onPick(o.id)}
            className="uppercase tracking-[0.14em]"
            style={{
              background: "transparent",
              border: "none",
              padding: "0 2px",
              cursor: "pointer",
              color: view === o.id ? "var(--color-accent)" : "var(--color-text-faint)",
              textShadow: view === o.id ? "0 0 8px var(--color-accent-glow)" : "none",
            }}
          >
            {o.label}
          </button>
        </Tooltip>
      ))}
    </div>
  );
}
