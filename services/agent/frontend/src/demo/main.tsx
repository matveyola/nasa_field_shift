import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource-variable/onest"; // the composer keeps its Onest
import "@fontsource-variable/inter/opsz.css";
import "./demo.css";
import { App } from "./App";

// The page is blank white with only the assistant (App.tsx); ?host=full shows the white demo host page and
// ?host=dark the original dark one (demo.css).
if (new URLSearchParams(location.search).get("host") === "dark") document.documentElement.dataset.host = "dark";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
