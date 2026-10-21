import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/index.css";
import "./styles/atmosphere.css";

// A new build replaces the hashed chunk files, so a tab opened on an earlier
// build can fail to load a chunk. Vite reports that as `vite:preloadError`:
// reload once to pick up the current index.html and chunk names. The sessionStorage
// stamp stops a reload loop if the failure is something other than a deploy.
window.addEventListener("vite:preloadError", (event) => {
  const KEY = "chunk-reload-at";
  const last = Number(sessionStorage.getItem(KEY) || 0);
  if (Date.now() - last > 10_000) {
    sessionStorage.setItem(KEY, String(Date.now()));
    event.preventDefault(); // we handle it: don't also throw
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
      // Swallow: PWA is progressive enhancement, not load-bearing.
    });
  });
}
