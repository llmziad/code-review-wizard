#!/usr/bin/env bash
#
# One-time install for the full stack: sidecar (Python) + app (Electron).
# Idempotent — safe to re-run after pulling new changes.
#
# Usage:
#   ./scripts/install.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
# Prerequisites
# ---------------------------------------------------------------------------

check_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "❌ Required command not found: $1"
    echo "   $2"
    return 1
  fi
}

echo "==> Checking prerequisites..."
check_cmd uv   "Install from https://docs.astral.sh/uv/getting-started/installation/"
check_cmd node "Install Node 22+ from https://nodejs.org"
check_cmd npm  "(ships with Node)"
check_cmd git  "Install from https://git-scm.com"

# Recommended but not required — we warn rather than fail.
if ! command -v gh >/dev/null 2>&1; then
  echo "ℹ  GitHub CLI (gh) not installed — that's fine; you'll log in via the OAuth"
  echo "   device flow from inside the app. Install gh for zero-friction auth:"
  echo "   brew install gh   (macOS)"
fi

# ---------------------------------------------------------------------------
# Sidecar (Python)
# ---------------------------------------------------------------------------

echo ""
echo "==> Syncing sidecar dependencies..."
(cd sidecar && uv sync)

# ---------------------------------------------------------------------------
# App (Electron + React + TS)
# ---------------------------------------------------------------------------

echo ""
echo "==> Installing app dependencies (this can take a minute)..."
(cd app && npm install)

echo ""
echo "==> Generating TypeScript types from the sidecar's JSON Schema..."
(cd app && npm run generate:types >/dev/null)

# ---------------------------------------------------------------------------
# Done
# ---------------------------------------------------------------------------

echo ""
echo "✓ Install complete."
echo ""
echo "Next steps:"
echo "  1. Add your Anthropic key:"
echo "       echo 'ANTHROPIC_API_KEY=sk-ant-...' >> sidecar/.env"
echo "  2. Launch the desktop app:"
echo "       ./scripts/start.sh"
