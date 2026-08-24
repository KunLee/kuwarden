/** Entry point. */

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { SessionProvider } from "./auth";
import { initDensity, initTheme } from "./theme";
import "./index.css";

// Before the first paint. Applying the stored theme from a React effect instead shows the
// light palette for one frame, which reads as a flash on every reload for a dark-mode user.
initTheme();
// Applied before React mounts, for the same reason as the theme: doing it from an effect
// paints one frame at the wrong scale, which reads as a jump on every reload.
initDensity();

const root = document.getElementById("root");
if (!root) throw new Error("no #root element");

createRoot(root).render(
  <StrictMode>
    <BrowserRouter>
      <SessionProvider>
        <App />
      </SessionProvider>
    </BrowserRouter>
  </StrictMode>,
);
