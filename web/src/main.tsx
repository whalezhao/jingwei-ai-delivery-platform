import { createRoot } from "react-dom/client";
import "@fontsource-variable/geist";
import "@fontsource-variable/geist-mono";
import "./i18n";
import "./styles/theme.css";
import "./global.css";
import App from "./App";

createRoot(document.getElementById("root")!).render(<App />);