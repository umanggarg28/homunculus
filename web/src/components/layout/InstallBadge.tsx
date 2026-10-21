import { useEffect, useState } from "react";
import { Tooltip } from "@/components/ui/Tooltip";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

export function InstallBadge() {
  const [deferred, setDeferred] = useState<BeforeInstallPromptEvent | null>(null);
  const [installed, setInstalled] = useState(false);

  useEffect(() => {
    if (window.matchMedia?.("(display-mode: standalone)").matches) {
      setInstalled(true);
      return;
    }
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setDeferred(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => {
      setInstalled(true);
      setDeferred(null);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (installed || !deferred) return null;

  return (
    <Tooltip
      text={<>Install Homunculus to your home screen / dock. Launches standalone (no browser chrome) and works offline for the shell. <strong>Server still required</strong> for live data.</>}
      placement="top"
    >
      <button
        onClick={async () => {
          await deferred.prompt();
          const { outcome } = await deferred.userChoice;
          if (outcome === "accepted") setDeferred(null);
        }}
        className="text-[10px] uppercase tracking-[0.1em] px-2 py-1 transition-colors"
        style={{
          color: "var(--color-accent)",
          border: "1px solid var(--color-accent)",
          background: "transparent",
          fontFamily: "var(--font-mono)",
        }}
      >
        [+] install app
      </button>
    </Tooltip>
  );
}
