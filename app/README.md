# Code Review Wizard — Desktop App

Electron + React + TypeScript frontend that wraps the [sidecar](../sidecar/) Python backend.

## Status

In active development. Today: scaffold + Tailwind compile cleanly. Next: sidecar IPC bridge, inbox view, PR detail with Monaco diff viewer, triage workflow, login flow.

## Develop

```bash
# One-time
npm install

# Generate TypeScript types from the sidecar's JSON Schema export
npm run generate:types

# Hot-reload dev mode (opens a window)
npm run dev

# Type check
npm run typecheck

# Production build
npm run build
```

## Stack

| Layer | Choice |
|---|---|
| Build | electron-vite (separate configs for main / preload / renderer) |
| Renderer | React 19 + TypeScript |
| Styling | Tailwind CSS v4 (via `@tailwindcss/vite`) |
| Server state | TanStack Query (against the sidecar's RPC) |
| Client state | Zustand |
| IPC | contextBridge + ipcMain.handle / webContents.send |
| Type generation | `reviewer schema` → quicktype → `src/shared/types.ts` |
