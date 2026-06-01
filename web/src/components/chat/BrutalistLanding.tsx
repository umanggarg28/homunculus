import { motion } from "framer-motion";

interface Props {
  bootDone: boolean;
  onPick: (text: string) => void;
}

const PROMPTS = [
  { label: "status", text: "what's still pending from yesterday's tasks?" },
  { label: "digest", text: "summarise what changed in my repo today" },
  { label: "research", text: "fetch the latest mcp spec changelog and pull out what affects our manager" },
  { label: "recall", text: "what do you know about my current goals?" },
];

/** Chat landing — shown when no messages exist.
 *  Full-bleed phosphor wordmark + prompt-picker grid.
 */
export function BrutalistLanding({ bootDone, onPick }: Props) {
  if (!bootDone) return null;

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="py-10"
    >
      {/* Wordmark */}
      <div className="mb-12">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.35, delay: 0.05 }}
          style={{
            fontFamily: "var(--font-mono)",
            fontSize: "clamp(48px, 10vw, 96px)",
            fontWeight: 700,
            lineHeight: 0.88,
            letterSpacing: "0",
            color: "var(--color-accent)",
            textShadow: "0 0 40px var(--color-accent-glow)",
          }}
        >
          HOMUNCULUS
        </motion.div>
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.25, delay: 0.2 }}
          className="mt-3 text-[11px] uppercase tracking-[0.28em]"
          style={{ color: "var(--color-text-muted)" }}
        >
          ── personal autonomous agent · ready
        </motion.div>
      </div>

      {/* Divider */}
      <div
        className="mb-8 text-[10px] uppercase tracking-[0.22em]"
        style={{ color: "var(--color-text-faint)" }}
      >
        ── quick start ──
      </div>

      {/* Prompt grid */}
      <div className="grid grid-cols-2 gap-3">
        {PROMPTS.map((p, i) => (
          <motion.button
            key={p.label}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2, delay: 0.15 + i * 0.06 }}
            onClick={() => onPick(p.text)}
            className="group text-left p-4 transition-all duration-150 cursor-pointer"
            style={{
              background: "var(--color-surface-2)",
              border: "1px solid var(--color-border)",
              fontFamily: "var(--font-mono)",
            }}
            whileHover={{ scale: 1.01 }}
            whileTap={{ scale: 0.99 }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = "var(--color-accent)";
              (e.currentTarget as HTMLElement).style.background = "var(--color-accent-faint)";
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLElement).style.borderColor = "var(--color-border)";
              (e.currentTarget as HTMLElement).style.background = "var(--color-surface-2)";
            }}
          >
            <div
              className="text-[9px] uppercase tracking-[0.22em] mb-2"
              style={{ color: "var(--color-accent)" }}
            >
              {p.label}
            </div>
            <div
              className="text-[12.5px] leading-[1.5]"
              style={{ color: "var(--color-text-dim)" }}
            >
              {p.text}
            </div>
          </motion.button>
        ))}
      </div>

      {/* Keyboard hint */}
      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.2, delay: 0.5 }}
        className="mt-8 text-[10px] uppercase tracking-[0.18em]"
        style={{ color: "var(--color-text-faint)" }}
      >
        ↵ send · ⇧↵ newline · type anything to start
      </motion.div>
    </motion.div>
  );
}
