/** Renders a HH:MM:SS countdown string with CRT-cursor colons — the
 *  separators blink at 1Hz (hard step, no fade; see .hm-tick-sep).
 *  Digits stay tabular and untouched so nothing shifts. */
export function TickingDigits({ text }: { text: string }) {
  return (
    <>
      {text.split(":").map((part, i, arr) => (
        <span key={i}>
          {part}
          {i < arr.length - 1 && <span className="hm-tick-sep">:</span>}
        </span>
      ))}
    </>
  );
}
