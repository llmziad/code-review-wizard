/**
 * Electron main process entry.
 *
 * - Creates the BrowserWindow with the preload bridge attached.
 * - Spawns the Python sidecar at app startup; tears it down on quit.
 * - Registers ipcMain handlers that translate renderer calls into sidecar requests.
 */

import { app, BrowserWindow } from "electron";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { sidecar } from "./sidecar";
import { registerIpcHandlers } from "./ipc";

const __dirname = dirname(fileURLToPath(import.meta.url));

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    titleBarStyle: "hiddenInset",
    backgroundColor: "#09090b",
    webPreferences: {
      // electron-vite builds the preload as .mjs (ESM); pointing at .js
      // silently fails to load and leaves `window.reviewer` undefined.
      preload: join(__dirname, "../preload/index.mjs"),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  if (process.env["ELECTRON_RENDERER_URL"]) {
    win.loadURL(process.env["ELECTRON_RENDERER_URL"]);
  } else {
    win.loadFile(join(__dirname, "../renderer/index.html"));
  }

  return win;
}

app.whenReady().then(async () => {
  registerIpcHandlers();

  // Start the sidecar eagerly so the inbox can hit it as soon as the renderer mounts.
  // We don't await — if it fails, the IPC handler surfaces the error to the renderer.
  sidecar.start().catch((err) => {
    console.error("[main] sidecar failed to start:", err);
  });

  createWindow();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", async (event) => {
  // Give the sidecar a chance to shut down cleanly. We block quit briefly;
  // it's fine because the sidecar drains in <2s.
  event.preventDefault();
  await sidecar.stop();
  app.exit(0);
});
