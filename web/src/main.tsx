import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import App from "./App";
import "./index.css";

// index.html always provides this, but asserting it away with `!` means a
// change to that file fails with "Cannot read properties of null" instead
// of saying what is wrong.
const root = document.getElementById("root");
if (!root) throw new Error("index.html is missing its #root element.");

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
