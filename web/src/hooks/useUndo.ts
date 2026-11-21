import { useCallback, useEffect, useRef, useState } from "react";

/** One change that can be taken back. `run` undoes it and says what is back. */
export interface Undoable {
  id: number;
  /** What it undoes, as the button names it: "your edit of “The book”". */
  label: string;
  run: () => Promise<string>;
}

let nextId = 1;

/** A stack of changes that can be undone, newest on top. A second change never takes
 *  away the first one's Undo, and nothing expires on a timer: an Undo goes when it is
 *  used, or when the screen it belongs to is left. The newest Undo takes focus, so the
 *  keyboard and screen readers land on it straight after the change. */
export function useUndo() {
  const [stack, setStack] = useState<Undoable[]>([]);
  const button = useRef<HTMLButtonElement>(null);
  const top = stack[stack.length - 1];

  useEffect(() => {
    if (top) button.current?.focus();
  }, [top?.id]);

  const push = useCallback((label: string, run: () => Promise<string>) => {
    setStack((items) => [...items, { id: nextId++, label, run }]);
  }, []);

  /** Undo the newest change, and return what to say about it. */
  const undo = useCallback(async (): Promise<string | null> => {
    if (!top) return null;
    setStack((items) => items.filter((item) => item.id !== top.id));
    return top.run();
  }, [top]);

  const clear = useCallback(() => setStack([]), []);

  return { top, push, undo, clear, button };
}
