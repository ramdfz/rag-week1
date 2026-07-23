import React from "react";
import ReactDOM from "react-dom/client";

import App from "./App";
import "./index.css";

// Apply theme before React renders (default: dark). Runs from the bundled 'self'
// script so it complies with the app's strict Content-Security-Policy.
(() => {
  let theme = "dark";
  try {
    theme = localStorage.getItem("meridian_theme") || "dark";
  } catch {
    /* ignore storage errors */
  }
  document.documentElement.classList.toggle("dark", theme !== "light");
})();

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
