import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/index.css";
import "./styles/atmosphere.css";

// A redeploy replaces the hashed chunk files; a tab that loaded the previous
// index.html then 404s when it lazy-loads a route ("app is crashing" from the
// user's chair). Vite surfaces exactly that as `vite:preloadError` — reload
// once to pick up the fresh index.html and chunk names. The sessionStorage
// stamp stops a reload loop if the failure is something other than a deploy.
window.addEventListener("vite:preloadError", (event) => {
  const KEY = "chunk-reload-at";
  const last = Number(sessionStorage.getItem(KEY) || 0);
  if (Date.now() - last > 10_000) {
    sessionStorage.setItem(KEY, String(Date.now()));
    event.preventDefault(); // we handle it — don't also throw
    window.location.reload();
  }
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

if ("serviceWorker" in navigator && import.meta.env.PROD) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // Swallow — PWA is progressive enhancement, not load-bearing.
    });
  });
}
