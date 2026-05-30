import { useAudio } from "@/hooks/useAudio";

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
        position: "fixed",
        top: 14,
        right: 18,
        zIndex: 50,
        background: "transparent",
        border: `1px solid ${audio.enabled ? "var(--color-accent)" : "var(--color-border)"}`,
        color: audio.enabled ? "var(--color-accent)" : "var(--color-text-muted)",
        fontFamily: "var(--font-mono)",
        fontSize: 9,
        fontWeight: 500,
        letterSpacing: "0.14em",
        padding: "8px 10px",
        cursor: "pointer",
        textTransform: "uppercase",
        transition: "color 0.15s, border-color 0.15s",
      }}
      onMouseEnter={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = "var(--color-border-bright)"; }}
      onMouseLeave={(e) => { (e.currentTarget as HTMLButtonElement).style.borderColor = audio.enabled ? "var(--color-accent)" : "var(--color-border)"; }}
    >
      ♪ SOUND {audio.enabled ? "ON" : "OFF"}
    </button>
  );
}
