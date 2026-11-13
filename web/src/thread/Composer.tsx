import { useState } from "react";

interface ComposerProps {
  sending: boolean;
  onSend: (text: string) => void;
  onStop: () => void;
}

/** Where you write to the agent. Enter sends, Shift and Enter starts a new line. While
 *  a turn runs, the send button becomes Stop, which stops it on the server. */
export function Composer({ sending, onSend, onStop }: ComposerProps) {
  const [text, setText] = useState("");
  const submit = () => {
    if (sending || !text.trim()) return;
    onSend(text);
    setText("");
  };
  return (
    <>
      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <label className="visually-hidden" htmlFor="msg">
          Message
        </label>
        <textarea
          id="msg"
          name="message"
          rows={1}
          placeholder="Ask it to do something"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              submit();
            }
          }}
        />
        {sending ? (
          <button className="send send--stop" type="button" aria-label="Stop" onClick={onStop}>
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor" />
            </svg>
          </button>
        ) : (
          <button className="send" type="submit" aria-label="Send" disabled={!text.trim()}>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 19V5M6 11l6-6 6 6" />
            </svg>
          </button>
        )}
      </form>
      <p className="talk-hint">Enter to send. Shift and Enter for a new line.</p>
    </>
  );
}
