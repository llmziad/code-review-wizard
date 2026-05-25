# Code Review Wizard

A code review tool for engineers who actually want their reviews read. Backed by Claude; built around the idea that **what the model sees matters more than which model it is**.

Two surfaces, one backend:

- **CLI** — works today. Auth, list PRs across all your repos, run a structured maintainability review, post a single GitHub review.
- **Desktop app** — in progress. Electron + React on top of the sidecar's JSON-RPC API. Inbox view of every PR awaiting your review; per-comment approve/dismiss/edit before posting.

---

## Repo layout

```
code-review-wizard/
├── sidecar/        Python backend — CLI today, JSON-RPC sidecar tomorrow
├── app/            Electron + React + TypeScript desktop app (in progress)
├── scripts/        install + start scripts for the full stack
├── README.md       you are here
└── ARCHITECTURE.md design rationale, four-stage pipeline, what's stubbed
```

The sidecar runs standalone — engineers who want only the CLI never need `app/`.

---

## Quickstart (CLI)

```bash
cd sidecar
uv sync

# Auth via GitHub OAuth (browser-based, no PAT to manage)
uv run reviewer login --device

# Configure Anthropic
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> ../.env

# See what's on your plate
uv run reviewer list

# Inspect a PR — assembles the context the reviewer would send, no LLM call
uv run reviewer inspect https://github.com/owner/repo/pull/123

# Run the review
uv run reviewer review https://github.com/owner/repo/pull/123

# Post to GitHub as a single review
uv run reviewer review https://github.com/owner/repo/pull/123 --post
```

If you already use the `gh` CLI, you can skip `login` entirely — the cascade picks up `gh auth token` automatically.

---

## Commands

| Command | Purpose |
|---|---|
| `reviewer login --device` | OAuth Device Flow — browser-based, recommended. |
| `reviewer login --token <PAT>` | Paste a Personal Access Token instead. |
| `reviewer logout` | Clear stored credentials. |
| `reviewer whoami` | Show authenticated user + active credential source. |
| `reviewer list` | PRs awaiting your review, your open PRs, ones assigned to you. |
| `reviewer inspect <url>` | Build and print the context bundle (no LLM call, no posting). |
| `reviewer review <url>` | Full pipeline → terminal output. |
| `reviewer review <url> --post` | Full pipeline → posts a single GitHub review. |

Every command supports `--json` for machine-readable output.

---

## Authentication

The CLI resolves a GitHub token in this order:

1. `GITHUB_TOKEN` / `GH_TOKEN` environment variable — for CI and power users.
2. `gh auth token` from the GitHub CLI — zero-friction if you already use `gh`.
3. Stored credentials at `~/.config/code-review-wizard/auth.json` (0600 perms).
4. Friendly error with install/login instructions tailored to what's missing.

`reviewer login --device` opens a browser to GitHub, you approve the request, the token lands in stored credentials. No PAT management.

For environments that need their own OAuth App (forks, internal deploys), override the bundled client_id with `CRW_OAUTH_CLIENT_ID`.

---

## Configuration

`.env` at the repo root:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for `review` |
| `GITHUB_TOKEN` | — | Optional; auto-detected from `gh` or stored auth if unset |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Override the model |
| `MIN_CONFIDENCE` | `0.7` | Comments below this confidence are dropped |
| `MAX_BLOCKING_COMMENTS` | `3` | Per-severity cap |
| `MAX_SUGGESTION_COMMENTS` | `5` | Per-severity cap |
| `MAX_NIT_COMMENTS` | `3` | Per-severity cap |
| `CACHE_DIR` | `./cache` | Where cloned repos and run logs live |
| `CRW_OAUTH_CLIENT_ID` | bundled | Override the GitHub OAuth App for login |

---

## What you get

The review focuses on **readability, structure, and maintainability** — not style nits, not correctness bugs, not security issues (those are separate passes the architecture is built for but doesn't ship yet).

Every run persists a complete record under `cache/runs/<id>/`:

- `context.json` — the full context bundle the LLM saw
- `passes/<name>.json` — the raw output of each analysis pass
- `dropped.json` — every filtered-out comment, organized by which filter killed it
- `report.json` — the final report rendered or posted

These artifacts are replayable, auditable, and the substrate for the feedback loop that will tune thresholds against real resolve/dismiss signals.

---

## Supported languages

Tree-sitter symbol extraction works on **Python, TypeScript, JavaScript, TSX, JSX** out of the box. Other languages fall back to a file-level pseudo-symbol — the model still sees them but reasons at file granularity.

---

## Roadmap

| In progress | Soon | Eventually |
|---|---|---|
| Electron desktop app | Per-comment triage (approve/dismiss/edit before post) | Code signing + notarization (paid Mac App) |
| `reviewer serve` JSON-RPC mode | Streaming progress events | Auto-update |
| Schema export for TS bindings | Per-repo conventions file | Second pass: correctness |
| | Prompt caching | Third pass: security |
| | OS keychain token storage | Adaptive pass routing |

The architecture (see `ARCHITECTURE.md`) is designed so each lands as a contained change, not a rewrite.

---

## Development

```bash
cd sidecar
uv run pytest          # all tests, offline, ~0.5s
uv run ruff check src/ tests/
```

See [`sidecar/README.md`](./sidecar/README.md) for backend dev details and [`app/README.md`](./app/README.md) for the desktop app's planned shape.
