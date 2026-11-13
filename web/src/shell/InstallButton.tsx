import { useEffect, useState } from "react";

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
};

/** Offers to install the console as an app, only where the browser says it can and it
 *  is not installed already. The server is still needed for live data. */
export function InstallButton() {
  const [offer, setOffer] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    if (matchMedia("(display-mode: standalone)").matches) return;
    const onOffer = (e: Event) => {
      e.preventDefault();
      setOffer(e as BeforeInstallPromptEvent);
    };
    const onInstalled = () => setOffer(null);
    addEventListener("beforeinstallprompt", onOffer);
    addEventListener("appinstalled", onInstalled);
    return () => {
      removeEventListener("beforeinstallprompt", onOffer);
      removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  if (!offer) return null;
  return (
    <button
      type="button"
      className="btn btn--quiet install"
      title="Opens in its own window. The server still needs to be running."
      onClick={async () => {
        await offer.prompt();
        await offer.userChoice;
        setOffer(null);
      }}
    >
      Install app
    </button>
  );
}
