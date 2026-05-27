#!/usr/bin/env bash
#
# Launch the desktop app in dev mode. Auto-heals missing types or deps so
# a fresh clone + this script just works.
#
# Usage:
#   ./scripts/start.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# Sidecar deps missing? Sync them. (First-time start without install.sh.)
if [ ! -d "$ROOT/sidecar/.venv" ]; then
  echo "==> Sidecar venv missing — running uv sync..."
  (cd sidecar && uv sync)
fi

# App deps missing?
if [ ! -d "$ROOT/app/node_modules" ]; then
  echo "==> App node_modules missing — running npm install..."
  (cd app && npm install)
fi

# TypeScript types missing? (They're committed, but a build-from-scratch may
# regenerate them off-cycle.)
if [ ! -f "$ROOT/app/src/shared/types.ts" ]; then
  echo "==> Types missing — running generate:types..."
  (cd app && npm run generate:types >/dev/null)
fi

# Friendly nudge if there's no Anthropic key — the inbox will still work,
# but Run Review will fail with a sidecar error.
if ! grep -qE '^ANTHROPIC_API_KEY=' "$ROOT/sidecar/.env" 2>/dev/null; then
  echo "ℹ  ANTHROPIC_API_KEY isn't set in sidecar/.env."
  echo "   Inbox works without it; Run Review needs it."
fi

cd "$ROOT/app"
echo "==> Launching app (Cmd+Q to quit)..."
exec npm run dev
