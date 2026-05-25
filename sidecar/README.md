# Code Review Wizard — Sidecar

Python backend for the Code Review Wizard. Ships as a standalone CLI today; runs as a long-lived sidecar process for the desktop app.

See the [top-level README](../README.md) for the product overview and the [ARCHITECTURE.md](../ARCHITECTURE.md) for the design.

## Running standalone

```bash
cd sidecar
uv sync
uv run reviewer login --device           # OAuth Device Flow
uv run reviewer list                     # PRs awaiting your review
uv run reviewer review <pr-url>          # full pipeline
uv run reviewer review <pr-url> --post   # post one GitHub review
```

## Running as a sidecar (for the desktop app)

```bash
uv run reviewer serve                    # JSON-RPC over stdio
```

The desktop app spawns this process and communicates via line-delimited JSON. See `src/reviewer/server.py` for the protocol.

## Tests

```bash
uv run pytest
```

All tests are offline (recorded fixtures + mocked LLM).
