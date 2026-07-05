import { useEffect, useState } from "react";

/** Sidebar phosphor cycler — same visual language as SoundToggle.
 *  Cycles the three real CRT chemistries: P1 green (default), P3
 *  amber, P4 white-blue. Only phosphor-family tokens change (see
 *  index.css); semantic colors keep their meaning on every glass.
 */
const PHOSPHORS = [
  { id: "green", label: "p1 · green" },
  { id: "amber", label: "p3 · amber" },
  { id: "white", label: "p4 · white" },
] as const;

type PhosphorId = (typeof PHOSPHORS)[number]["id"];

function currentPhosphor(): PhosphorId {
  const p = document.documentElement.dataset.phosphor;
  return p === "amber" || p === "white" ? p : "green";
}

export function PhosphorToggle() {
  const [phosphor, setPhosphor] = useState<PhosphorId>(() => currentPhosphor());

  // Stay truthful if the attribute changes outside this button
  // (another tab, devtools, tests) — same observer the robot uses.
  useEffect(() => {
    const mo = new MutationObserver(() => setPhosphor(currentPhosphor()));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-phosphor"] });
    return () => mo.disconnect();
  }, []);

  const cycle = () => {
    const idx = PHOSPHORS.findIndex((p) => p.id === phosphor);
    const next = PHOSPHORS[(idx + 1) % PHOSPHORS.length].id;
    if (next === "green") {
      delete document.documentElement.dataset.phosphor;
      localStorage.removeItem("hm-phosphor");
    } else {
      document.documentElement.dataset.phosphor = next;
      localStorage.setItem("hm-phosphor", next);
    }
    setPhosphor(next);
  };

  const label = PHOSPHORS.find((p) => p.id === phosphor)?.label ?? "p1 · green";

  return (
    <button
      onClick={cycle}
      style={{
        width: "100%",
        background: "transparent",
        border: "1px solid var(--color-border)",
        color: "var(--color-text-muted)",
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        fontWeight: 500,
        letterSpacing: "0.14em",
        padding: "6px 8px",
        cursor: "pointer",
        textTransform: "uppercase",
        textAlign: "left",
        transition: "color 0.12s, border-color 0.12s",
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
      onMouseEnter={(e) => {
        (e.currentTarget as HTMLElement).style.color = "var(--color-accent)";
        (e.currentTarget as HTMLElement).style.borderColor = "var(--color-accent)";
      }}
      onMouseLeave={(e) => {
        (e.currentTarget as HTMLElement).style.color = "var(--color-text-muted)";
        (e.currentTarget as HTMLElement).style.borderColor = "var(--color-border)";
      }}
    >
      <span style={{ opacity: 0.7 }}>◉</span>
      <span>phosphor {label}</span>
    </button>
  );
}
