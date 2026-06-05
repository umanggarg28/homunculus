import {
  Children,
  cloneElement,
  isValidElement,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";

type Placement = "top" | "bottom" | "left" | "right";

interface Props {
  text: ReactNode;
  placement?: Placement;
  delay?: number;
  /** Single React child (or string/number — wrapped in a span).
   *  The trigger element gets aria-describedby + onMouseEnter/Leave/Focus/Blur. */
  children: ReactNode;
}

/**
 * Brutalist phosphor tooltip — replaces native `title="..."` so the
 * hint matches the rest of the UI instead of the OS chrome.
 *
 *   <Tooltip text="Heartbeat fires every 60 min">
 *     <HeartbeatPulse />
 *   </Tooltip>
 *
 *  - Rendered via a portal so transforms/overflow on ancestors don't
 *    clip it.
 *  - Hover + keyboard-focus reveal; ESC dismisses.
 *  - Sub-pixel position recompute on resize/scroll so the bubble
 *    follows fixed/sticky triggers.
 *  - Falls back to wrapping plain children in a <span> so it stays a
 *    drop-in replacement for `title=""`.
 */
export function Tooltip({ text, placement = "top", delay = 250, children }: Props) {
  const [open, setOpen] = useState(false);
  const [coords, setCoords] = useState<{ top: number; left: number } | null>(null);
  const triggerRef = useRef<HTMLElement | null>(null);
  const bubbleRef = useRef<HTMLDivElement | null>(null);
  const showTimer = useRef<number | null>(null);
  const id = useId();

  const clearShow = () => {
    if (showTimer.current !== null) {
      window.clearTimeout(showTimer.current);
      showTimer.current = null;
    }
  };

  const show = () => {
    clearShow();
    showTimer.current = window.setTimeout(() => setOpen(true), delay);
  };
  const hide = () => {
    clearShow();
    setOpen(false);
  };

  useEffect(() => {
    if (!open) return;
    const reposition = () => {
      const trigger = triggerRef.current;
      const bubble = bubbleRef.current;
      if (!trigger || !bubble) return;
      const r = trigger.getBoundingClientRect();
      const b = bubble.getBoundingClientRect();
      const gap = 8;
      let top = 0;
      let left = 0;
      if (placement === "top") {
        top = r.top - b.height - gap;
        left = r.left + r.width / 2 - b.width / 2;
      } else if (placement === "bottom") {
        top = r.bottom + gap;
        left = r.left + r.width / 2 - b.width / 2;
      } else if (placement === "left") {
        top = r.top + r.height / 2 - b.height / 2;
        left = r.left - b.width - gap;
      } else {
        top = r.top + r.height / 2 - b.height / 2;
        left = r.right + gap;
      }
      // Clamp to viewport so the bubble never paints off-screen.
      const pad = 4;
      left = Math.max(pad, Math.min(left, window.innerWidth - b.width - pad));
      top = Math.max(pad, Math.min(top, window.innerHeight - b.height - pad));
      setCoords({ top, left });
    };
    reposition();
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [open, placement]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  useEffect(() => () => clearShow(), []);

  // Wrap raw children in a span; clone valid elements so we can
  // attach the ref + handlers without an extra DOM node when possible.
  const child = Children.only(children);
  const trigger = isValidElement(child) ? (
    cloneElement(child as ReactElement<any>, {
      ref: (node: HTMLElement | null) => {
        triggerRef.current = node;
        const original = (child as any).ref;
        if (typeof original === "function") original(node);
        else if (original && "current" in original) original.current = node;
      },
      onMouseEnter: (e: any) => {
        show();
        (child as any).props.onMouseEnter?.(e);
      },
      onMouseLeave: (e: any) => {
        hide();
        (child as any).props.onMouseLeave?.(e);
      },
      onFocus: (e: any) => {
        show();
        (child as any).props.onFocus?.(e);
      },
      onBlur: (e: any) => {
        hide();
        (child as any).props.onBlur?.(e);
      },
      "aria-describedby": open ? id : undefined,
    })
  ) : (
    <span
      ref={(node) => {
        triggerRef.current = node;
      }}
      onMouseEnter={show}
      onMouseLeave={hide}
      onFocus={show}
      onBlur={hide}
      aria-describedby={open ? id : undefined}
    >
      {child}
    </span>
  );

  return (
    <>
      {trigger}
      {open &&
        createPortal(
          <div
            id={id}
            ref={bubbleRef}
            role="tooltip"
            className="hm-tooltip"
            style={{
              position: "fixed",
              top: coords?.top ?? -9999,
              left: coords?.left ?? -9999,
              opacity: coords ? 1 : 0,
              pointerEvents: "none",
              zIndex: 9999,
              maxWidth: 280,
            }}
          >
            {text}
          </div>,
          document.body,
        )}
    </>
  );
}
