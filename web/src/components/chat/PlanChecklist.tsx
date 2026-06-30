import { motion } from "framer-motion";

interface Props {
  /** The plan tool's result — a markdown checklist:
   *  `- [ ] 1. Step text` / `- [x] 2. Step text — note` */
  source: string;
}

interface Step {
  done: boolean;
  text: string;
}

function parse(source: string): Step[] {
  const steps: Step[] = [];
  for (const raw of source.split("\n")) {
    const m = raw.match(/^\s*-\s*\[( |x)\]\s*\d+\.\s*(.+)$/i);
    if (m) steps.push({ done: m[1].toLowerCase() === "x", text: m[2].trim() });
  }
  return steps;
}

/** A multi-step plan rendered as a themed checklist (CRT / mono).
 * Replaces the generic tool-call body for plan_steps / complete_step so the
 * agent's plan-before-act is a visible, first-class element in the chat. */
export function PlanChecklist({ source }: Props) {
  const steps = parse(source);
  if (steps.length === 0) return null;

  const doneCount = steps.filter((s) => s.done).length;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.2, 0.8, 0.2, 1] }}
      className="
        my-2 border border-[var(--color-border-strong)]
        bg-[var(--color-surface-3)]/40 rounded-md px-3 py-2
      "
    >
      <div className="flex items-baseline justify-between mono-caps text-[var(--color-text-dim)] mb-2">
        <span className="text-[var(--color-signal)]">▤ plan</span>
        <span className="text-[var(--color-text-faint)]">
          {doneCount}/{steps.length}
        </span>
      </div>

      <ul className="flex flex-col gap-1">
        {steps.map((step, i) => (
          <motion.li
            key={i}
            layout
            className="flex items-start gap-2 text-[13px] leading-snug"
          >
            <span
              className={
                step.done
                  ? "text-[var(--color-signal)]"
                  : "text-[var(--color-text-faint)]"
              }
            >
              {step.done ? "▣" : "▢"}
            </span>
            <span
              className={
                step.done
                  ? "text-[var(--color-text-muted)] line-through"
                  : "text-[var(--color-text)]"
              }
            >
              {step.text}
            </span>
          </motion.li>
        ))}
      </ul>
    </motion.div>
  );
}
