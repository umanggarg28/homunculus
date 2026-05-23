import { useEffect, useState } from "react";

/** Wraps a string and renders it character-by-character with a tiny
 * fade-in. As `text` grows (the assistant is streaming), the new
 * characters appear with the fade; already-rendered text doesn't
 * re-animate. */
export function StreamingText({ text }: { text: string }) {
  const [rendered, setRendered] = useState<number>(text.length);

  useEffect(() => {
    if (text.length > rendered) {
      const id = requestAnimationFrame(() => setRendered(text.length));
      return () => cancelAnimationFrame(id);
    }
    if (text.length < rendered) {
      // Chat reset — snap back.
      setRendered(text.length);
    }
  }, [text, rendered]);

  return (
    <span>
      {text.slice(0, rendered).split("").map((ch, i) => (
        <span
          key={i}
          className="animate-fadein"
          style={{
            display: "inline",
            animation: "fadein 240ms ease-out forwards",
          }}
        >
          {ch}
        </span>
      ))}
      <style>{`
        @keyframes fadein {
          from { opacity: 0; filter: blur(1.5px); }
          to   { opacity: 1; filter: blur(0); }
        }
      `}</style>
    </span>
  );
}
