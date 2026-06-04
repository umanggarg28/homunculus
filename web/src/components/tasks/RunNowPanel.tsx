import { useEffect, useRef, useState } from "react";
import { API_BASE, authHeaders } from "@/lib/api";

/**
 * Right-side drawer that streams the agent's live execution of one task.
 *
 * UX: click `[run now]` on a TaskRow → this panel slides in from the right →
 * SSE chunks from POST /api/tasks/{id}/run-stream render as they arrive →
 * the panel stays open after completion so the user can see the full
 * transcript without trekking to Traces.
 *
 * Implementation: uses fetch() with a streaming ReadableStream rather than
 * EventSource because EventSource doesn't support POST or custom headers
 * (auth token).
 */
interface Props {
  taskId: string;
  taskTitle: string;
  open: boolean;
  onClose: () => void;
}

export function RunNowPanel({ taskId, taskTitle, open, onClose }: Props) {
  const [chunks, setChunks] = useState<string[]>([]);
  const [state, setState] = useState<"idle" | "running" | "done" | "error">("idle");
  const [errMsg, setErrMsg] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Start the run when the panel opens.
  useEffect(() => {
    if (!open) return;
    setChunks([]);
    setErrMsg(null);
    setState("running");
    const ctrl = new AbortController();
    abortRef.current = ctrl;

    (async () => {
      try {
        const resp = await fetch(`${API_BASE}/tasks/${encodeURIComponent(taskId)}/run-stream`, {
          method: "POST",
          headers: { ...authHeaders(), "Content-Type": "application/json" },
          signal: ctrl.signal,
        });
        if (!resp.ok) {
          setErrMsg(`HTTP ${resp.status}: ${await resp.text()}`);
          setState("error");
          return;
        }
        if (!resp.body) {
          setErrMsg("server did not return a stream body");
          setState("error");
          return;
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        // Read until done; parse SSE `data:` lines.
        // Format from web_api._format_sse_data → "data: <text>\n\n"
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          // SSE messages are separated by \n\n
          let sep: number;
          while ((sep = buffer.indexOf("\n\n")) !== -1) {
            const frame = buffer.slice(0, sep);
            buffer = buffer.slice(sep + 2);
            // Each frame may have multiple lines; concat the `data:` ones.
            const dataLines = frame
              .split("\n")
              .filter((l) => l.startsWith("data: "))
              .map((l) => l.slice(6));
            if (dataLines.length > 0) {
              setChunks((prev) => [...prev, dataLines.join("\n")]);
            }
          }
        }
        setState("done");
      } catch (e) {
        if ((e as { name?: string })?.name === "AbortError") return;
        setErrMsg(String(e));
        setState("error");
      }
    })();

    return () => { ctrl.abort(); };
  }, [open, taskId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chunks.length]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-label={`Running ${taskTitle}`}
      style={{
        position: "fixed",
        top: 0, right: 0, bottom: 0,
        width: "min(560px, 92vw)",
        background: "var(--color-surface-1)",
        borderLeft: "1px solid var(--color-border-strong)",
        boxShadow: "-12px 0 36px rgba(0,0,0,0.32)",
        fontFamily: "var(--font-mono)",
        display: "flex",
        flexDirection: "column",
        zIndex: 200,
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "14px 16px",
          borderBottom: "1px solid var(--color-border)",
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          gap: 12,
        }}
      >
        <div style={{ minWidth: 0 }}>
          <div className="brut-meta" style={{ color: "var(--color-text-faint)" }}>
            ── run now
          </div>
          <div
            className="text-[13px] uppercase tracking-[0.04em] mt-1"
            style={{
              color: "var(--color-text)",
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {taskTitle}
          </div>
        </div>
        <button
          onClick={() => { abortRef.current?.abort(); onClose(); }}
          className="text-[10px] uppercase tracking-[0.16em]"
          style={{
            background: "transparent",
            border: "1px solid var(--color-border)",
            color: "var(--color-text-muted)",
            padding: "4px 9px",
            cursor: "pointer",
          }}
        >
          [{state === "running" ? "stop" : "close"}]
        </button>
      </div>

      {/* State badge */}
      <div
        style={{
          padding: "8px 16px",
          borderBottom: "1px dashed var(--color-border)",
          fontSize: 9,
          letterSpacing: "0.18em",
          textTransform: "uppercase",
          color:
            state === "running" ? "var(--color-accent)"
            : state === "done" ? "var(--color-text-muted)"
            : state === "error" ? "var(--color-danger)"
            : "var(--color-text-faint)",
        }}
      >
        ● {state}
        {errMsg && <span style={{ color: "var(--color-danger)", marginLeft: 12 }}>{errMsg}</span>}
      </div>

      {/* Stream body */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: "12px 16px",
          fontSize: 12,
          lineHeight: 1.55,
          color: "var(--color-text-dim)",
          whiteSpace: "pre-wrap",
          overflowWrap: "anywhere",
          wordBreak: "break-word",
        }}
      >
        {chunks.length === 0 && state === "running" && (
          <div style={{ color: "var(--color-text-faint)" }}>waiting for first chunk…</div>
        )}
        {chunks.map((c, i) => (
          <span key={i}>{c}</span>
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
