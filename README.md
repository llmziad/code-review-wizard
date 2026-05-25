# Code Review Wizard

A code review tool for engineers who actually want their reviews read. Backed by Claude; built around the idea that **what the model sees matters more than which model it is**.

Today: a CLI that lists the PRs awaiting your review across every repo you can see, then runs a structured maintainability review on any of them and posts it as a single GitHub review.

Coming next: a desktop (Electron) app on top of the same CLI — same auth, same data, same review pipeline, just with an inbox-style UI.

---

## Quickstart

```bash
# Install
uv sync

# Authenticate (one time)
uv run reviewer login --token <github-pat>
# Or, if you have the gh CLI:        no login needed — we pick it up automatically.

# Configure the model
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> .env

# See what's on your plate
uv run reviewer list

# Inspect a PR — assembles the context the reviewer would send, no LLM call
uv run reviewer inspect https://github.com/owner/repo/pull/123

# Review it (terminal output)
uv run reviewer review https://github.com/owner/repo/pull/123

# Review it and post to GitHub as one review
uv run reviewer review https://github.com/owner/repo/pull/123 --post
```

A PAT needs `repo` (for private repos) and `read:user` scopes. Create one at <https://github.com/settings/tokens/new>.

---

## Commands

| Command | Purpose |
|---|---|
| `reviewer login` | Store GitHub credentials. `--token <PAT>` or interactive paste. |
| `reviewer logout` | Clear stored credentials. |
| `reviewer whoami` | Show the authenticated user and which credential source is active. |
| `reviewer list` | PRs awaiting your review, your own open PRs, and ones assigned to you. `--filter` to scope. |
| `reviewer inspect <url>` | Build and print the context bundle (no LLM call, no posting). |
| `reviewer review <url>` | Full pipeline → terminal output. |
| `reviewer review <url> --post` | Full pipeline → posts a single GitHub review. |

Every command supports `--json` for machine-readable output. This is the seam the desktop app will shell across.

```bash
uv run reviewer list --json | jq '.review_requested[].url'
uv run reviewer review <url> --json > report.json
```

---

## Authentication

The CLI resolves a GitHub token in this order:

1. `GITHUB_TOKEN` or `GH_TOKEN` environment variable — for CI and power users.
2. `gh auth token` from the GitHub CLI — zero-friction if you already use `gh`.
3. Stored credentials at `~/.config/code-review-wizard/auth.json` (0600 perms).
4. Friendly error: "run `reviewer login`".

This means **engineers with `gh` installed need zero setup beyond `ANTHROPIC_API_KEY`** to start reviewing PRs.

For environments where you'd rather not paste a PAT (laptop without `gh`, shared machine), an OAuth Device Flow path is scaffolded but requires registering a GitHub OAuth App and setting `CRW_OAUTH_CLIENT_ID`. The PAT path remains available regardless.

---

## Configuration

`.env`:

| Variable | Default | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for `review` |
| `GITHUB_TOKEN` | — | Optional; auto-detected from `gh` or stored auth if unset |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Override the model |
| `MIN_CONFIDENCE` | `0.7` | Comments below this confidence are dropped |
| `MAX_BLOCKING_COMMENTS` | `3` | Per-severity cap on what gets surfaced |
| `MAX_SUGGESTION_COMMENTS` | `5` | Per-severity cap |
| `MAX_NIT_COMMENTS` | `3` | Per-severity cap |
| `CACHE_DIR` | `./cache` | Where cloned repos and run logs live |
| `CRW_OAUTH_CLIENT_ID` | — | Optional. Enables `login --device` OAuth flow. |

---

## What you actually get

The review focuses on **readability, structure, and maintainability** — not style nits, not correctness bugs, not security issues (those are separate passes the architecture is built for but doesn't ship yet).

Every run persists a complete record under `cache/runs/<id>/`:

- `context.json` — the full context bundle the LLM saw
- `passes/<name>.json` — the raw output of each analysis pass
- `dropped.json` — every filtered-out comment, organized by which filter killed it
- `report.json` — the final report rendered or posted

These artifacts are replayable, auditable, and the substrate for the feedback loop that will tune thresholds against real resolve/dismiss signals as the system matures.

---

## Supported languages

Tree-sitter symbol extraction works on **Python, TypeScript, JavaScript, TSX, JSX** out of the box. Other languages fall back to a file-level pseudo-symbol — the model still sees them, but reasons at file granularity rather than function.

---

## Roadmap

| Soon | Eventually |
|---|---|
| OS keychain token storage (replaces the file) | Electron desktop app |
| Real-time PR list refresh | GitHub App webhook deployment |
| GitHub OAuth App pre-registered | Multi-repo CI integration |
| Per-repo conventions file | Second pass: correctness |
| Prompt caching for ~20% cost reduction | Third pass: security |
| Feedback loop training | Adaptive pass routing |

The architecture (see `ARCHITECTURE.md`) is designed for each of these to land as a contained change, not a rewrite.

---

## Project layout

```
src/reviewer/
  models.py              Single source of truth for cross-module types.
  auth.py                Token storage + resolution cascade + OAuth scaffolding.
  config.py              Pydantic settings (env-driven).
  github_client.py       PyGithub + httpx + git for everything GitHub-shaped.
  pipeline.py            Stage 1→2→3→ReviewReport orchestrator.
  cli.py                 Typer entry point — all human and machine surfaces.
  context/               Stage 1 — context assembly.
    diff_parser.py       Unified diff → list[ChangedSymbol] via tree-sitter.
    symbol_resolver.py   Callers + siblings via ripgrep + AST filter.
    conventions.py       Repo conventions snippet (stubbed; production retrieves).
    assembler.py         Stage 1 orchestrator.
  analysis/              Stage 2 — LLM passes.
    base.py              AnalysisPass ABC.
    router.py            Decides which passes to run for a bundle.
    maintainability.py   The one shipping pass.
    prompts/             System prompts as Markdown.
  filtering/             Stage 3 — confidence/dedup/priority.
  delivery/              Stage 4 — terminal renderer + GitHub poster.
```

See `ARCHITECTURE.md` for the design rationale, the four-stage pipeline, the architectural decisions worth defending, and where production deviates from the MVP.

---

## Development

```bash
# All tests, offline
uv run pytest

# Lint
uv run ruff check src/ tests/

# Live smoke test (uses your gh token)
uv run reviewer list
uv run reviewer inspect <some-real-PR-url>
```

Tests are offline by design — diff parsing uses recorded fixtures, the pipeline test mocks the LLM, and the auth tests use an isolated config dir. Live GitHub access is exercised by the integration commands above against real PRs.
