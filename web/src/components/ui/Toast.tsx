import { AnimatePresence, motion } from "framer-motion";

interface Props {
  message: string | null;
  onDismiss: () => void;
}

export function Toast({ message, onDismiss }: Props) {
  return (
    <AnimatePresence>
      {message && (
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -8 }}
          transition={{ duration: 0.3 }}
          onAnimationComplete={() => {
            window.setTimeout(onDismiss, 2200);
          }}
          className="fixed bottom-8 left-1/2 -translate-x-1/2 z-50 px-4 py-2.5 pointer-events-none"
          style={{
            background: "var(--color-text)",
            color: "var(--color-bg)",
            fontFamily: "var(--font-sans)",
            fontSize: 15,
            boxShadow: "0 8px 24px rgba(60, 30, 15, 0.25)",
          }}
        >
          {message}
        </motion.div>
      )}
    </AnimatePresence>
  );
}
