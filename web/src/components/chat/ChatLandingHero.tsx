import { motion } from "framer-motion";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import type { Chapter } from "@/lib/types";

interface Props { onPick: (text: string) => void; }

const SUGGESTIONS = [
  "What should we think about today?",
  "Remind me what I was working on yesterday.",
  "Help me plan the next hour.",
];

export function ChatLandingHero({ onPick }: Props) {
  const [lastChapter, setLastChapter] = useState<Chapter | null>(null);

  useEffect(() => {
    api.chaptersList()
      .then((list) => setLastChapter(list[0] ?? null))
      .catch(() => setLastChapter(null));
  }, []);

  return (
    <div className="pt-6 pb-10">
      <motion.h1
        className="text-[var(--color-text)] mb-3"
        style={{ fontSize: 28, fontWeight: 600, letterSpacing: "0", lineHeight: 1.2 }}
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        What's on your mind?
      </motion.h1>
      <motion.p
        className="text-[14px] mb-8"
        style={{ color: "var(--color-text-muted)", maxWidth: 520, lineHeight: 1.55 }}
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.3, delay: 0.1 }}
      >
        Your assistant remembers prior conversations, runs its own tools, and
        works between your messages.
      </motion.p>

      <motion.div
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ duration: 0.3, delay: 0.2 }}
      >
        <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
          Try one
        </div>
        <div className="flex flex-col">
          {SUGGESTIONS.map((s, i) => (
            <motion.button
              key={s}
              onClick={() => onPick(s)}
              initial={{ opacity: 0, x: -3 }}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.25, delay: 0.25 + i * 0.04 }}
              className="group flex items-center justify-between gap-3 px-3 h-10 rounded-[6px] text-left transition-colors text-[13.5px] text-[var(--color-text-dim)] hover:bg-[var(--color-surface-2)] hover:text-[var(--color-text)]"
            >
              <span>{s}</span>
              <span className="opacity-0 group-hover:opacity-100 transition-opacity text-[var(--color-accent)]">→</span>
            </motion.button>
          ))}
        </div>
      </motion.div>

      {lastChapter && (
        <motion.div
          className="mt-12 pt-5"
          style={{ borderTop: "1px solid var(--color-border)" }}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.3, delay: 0.45 }}
        >
          <div className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
            Last conversation
          </div>
          <Link to="/logs" className="flex items-baseline justify-between gap-4 group">
            <span className="truncate text-[13.5px] text-[var(--color-text-dim)] group-hover:text-[var(--color-text)] transition-colors">
              {lastChapter.title}
            </span>
            <span className="text-[12px] text-[var(--color-text-muted)] tabular shrink-0">
              {lastChapter.closed_at?.slice(0, 10) ?? ""}
            </span>
          </Link>
        </motion.div>
      )}
    </div>
  );
}
