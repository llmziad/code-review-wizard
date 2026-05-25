/**
 * Preload script — runs with limited Node access, exposes a typed API to the
 * renderer via Electron's contextBridge.
 *
 * Today this is a placeholder. The full sidecar bridge lands in the next
 * commit (window.reviewer.listPrs, .reviewPr, .login, etc.).
 */

import { contextBridge } from "electron";

contextBridge.exposeInMainWorld("reviewer", {
  // Sentinel so the renderer can detect "am I running inside Electron with the
  // bridge wired up?" before the real methods land.
  ready: true as const,
});

declare global {
  interface Window {
    reviewer: {
      ready: true;
    };
  }
}
