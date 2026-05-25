/**
 * Electron main process entry.
 *
 * Today this just creates a BrowserWindow. In the next commit it also spawns
 * the Python sidecar (`reviewer serve`) and exposes a typed IPC bridge to
 * the renderer via preload.
 */

import { app, BrowserWindow } from "electron";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));

function createWindow(): BrowserWindow {
  const win = new BrowserWindow({
    width: 1200,
    height: 800,
    titleBarStyle: "hiddenInset", // macOS: traffic lights only, no titlebar
    backgroundColor: "#09090b", // matches the zinc-950 body bg
    webPreferences: {
      preload: join(__dirname, "../preload/index.js"),
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

app.whenReady().then(() => {
  createWindow();

  app.on("activate", () => {
    // macOS: re-create a window when the dock icon is clicked and there are none
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  // macOS apps usually stay alive until Cmd+Q; everywhere else quit
  if (process.platform !== "darwin") app.quit();
});
