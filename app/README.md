# Code Review Wizard — Desktop App

Electron + React + TypeScript frontend that wraps the [sidecar](../sidecar/) Python backend.

**Status: not yet implemented.** This directory is reserved for the Electron source. See the [top-level README](../README.md) and [ARCHITECTURE.md](../ARCHITECTURE.md) for the planned shape.

Planned stack:
- electron-vite (build)
- React 19 + TypeScript (renderer)
- Tailwind CSS v4 (styling)
- Monaco (diff viewer)
- TanStack Query (server state) + Zustand (client state)
- Electron `safeStorage` API (token storage via OS keychain)

The renderer talks to the sidecar via JSON-RPC over stdio. TypeScript types are generated from the sidecar's Pydantic models via `reviewer schema` → quicktype.
