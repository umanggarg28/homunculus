import { useAudio } from "@/hooks/useAudio";

/** Inline sidebar sound toggle — same visual language as ModeToggle. */
export function SoundToggle() {
  const audio = useAudio();

  const handleClick = () => {
    if (!audio.ready) {
      audio.init();
      audio.powerOn();
    }
    audio.setEnabled(!audio.enabled);
  };

  return (
    <button
      onClick={handleClick}
      style={{
        width: "100%",
        background: "transparent",
        border: `1px solid ${audio.enabled ? "var(--color-accent)" : "var(--color-border)"}`,
        color: audio.enabled ? "var(--color-accent)" : "var(--color-text-muted)",
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        fontWeight: 500,
        letterSpacing: "0.14em",
        padding: "6px 8px",
        cursor: "pointer",
        textTransform: "uppercase",
        textAlign: "left",
        transition: "color 0.12s, border-color 0.12s",
        display: "flex",
        alignItems: "center",
        gap: 6,
      }}
    >
      <span style={{ opacity: 0.7 }}>♪</span>
      <span>sound {audio.enabled ? "on" : "off"}</span>
    </button>
  );
}
