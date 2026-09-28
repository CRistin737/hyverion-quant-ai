import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./styles/app.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { isTauri } from "@tauri-apps/api/core";

import { reportClientError } from "./api/client";
import { App } from "./App";

window.addEventListener("error", (event) => reportClientError(`window error: ${event.message}`));
window.addEventListener("unhandledrejection", (event) => reportClientError(`unhandled rejection: ${String(event.reason)}`));
document.addEventListener("securitypolicyviolation", (event) => reportClientError(`CSP blocked ${event.blockedURI} (${event.violatedDirective})`));

if (isTauri()) document.documentElement.dataset.shell = "tauri";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
