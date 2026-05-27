# Code Review Wizard

A code review tool for engineers who actually want their reviews read. Backed by Claude; built around the idea that **what the model sees matters more than which model it is**.

Two surfaces, one backend:

- **Desktop app** (Electron) — inbox of PRs awaiting your review, click into one, watch the review pipeline stream live, approve/dismiss/edit comments before posting as a single GitHub review.
- **CLI** — same backend, scriptable. Useful for CI integration, `--json` output, and the moments when a window is overkill.

## Quickstart — desktop app

```bash
git clone https://github.com/llmziad/code-review-wizard.git
cd code-review-wizard

./scripts/install.sh          # one-time: syncs sidecar, installs app deps, generates types
echo 'ANTHROPIC_API_KEY=sk-ant-...' >> sidecar/.env
./scripts/start.sh            # launches the window
```

On first launch you'll get a "Sign in with GitHub" screen → device-flow login → your inbox.

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

## Quickstart — CLI only

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
| `reviewer serve` | Run as a JSON-RPC sidecar (for the desktop app). |
| `reviewer schema [--out path]` | Emit JSON Schema for all public sidecar types. |

Every interactive command supports `--json` for machine-readable output.

---

## Sidecar mode (for the desktop app)

The Electron app spawns `reviewer serve` and communicates over stdin/stdout with line-delimited JSON. The same backend that powers the CLI powers the GUI; no duplication, one source of truth.

```bash
# What the desktop app does internally
reviewer serve   # waits for JSON requests on stdin
```

**Protocol**:

```jsonl
→ {"id": "1", "method": "list_prs", "params": {"filter": "review-requested"}}
← {"id": "1", "result": {"review_requested": [...]}}

→ {"id": "2", "method": "review_pr", "params": {"url": "https://..."}}
← {"event": "started", "request_id": "2", "stage": "context"}
← {"event": "progress", "request_id": "2", "stage": "context", "detail": "Cloning repo (shallow)"}
← {"event": "pass_completed", "request_id": "2", "stage": "analysis", "tokens_input": 7234, ...}
← {"event": "completed", "request_id": "2", "stage": "filtering"}
← {"id": "2", "result": {"run_id": "...", "report": {...}}}

→ {"id": "3", "method": "post_review", "params": {"run_id": "...", "comment_ids": ["abc", "def"]}}
← {"id": "3", "result": {"review_url": "https://github.com/..."}}
```

**Available methods**:

| Method | Returns | Notes |
|---|---|---|
| `whoami` | user info | |
| `login_pat` | StoredAuth | input: `{token}` |
| `login_device_start` | DeviceFlowChallenge | step 1 of OAuth device flow |
| `login_device_poll` | StoredAuth | step 2; emits `login_polling` countdown events |
| `logout` | `{removed: bool}` | |
| `list_prs` | grouped PRSummary list | input: `{filter, limit}` |
| `inspect_pr` | ContextBundle | no LLM call |
| `review_pr` | `{run_id, report}` | **streams** PipelineEvent during execution |
| `get_run` | ReviewReport | retrieve a cached run |
| `post_review` | `{review_url}` | input: `{run_id, comment_ids?, edits?}` for triage |
| `dismiss_comments` | `{dismissed: int}` | logs to `dropped.json` for the feedback loop |
| `schema` | JSON Schema doc | the typed contract surface |

Each event carries `request_id` so the renderer can route progress to the right UI surface. Requests run concurrently in their own asyncio tasks — a long `review_pr` doesn't block a `list_prs` from the sidebar.

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

## What's shipped vs roadmap

**Shipped (v0.1):**
- CLI: login (device flow + PAT), whoami, list, inspect, review, post, JSON mode for every command
- Sidecar mode: `reviewer serve` JSON-RPC over stdio with streaming pipeline events
- Triage actions on cached runs: `post_review`, `dismiss_comments`, `get_run`
- Schema export → TypeScript type generation
- Desktop app: inbox · PR detail · live streaming review · per-comment triage · single-review post · login/settings screens

**Roadmap:**
| Soon | Eventually |
|---|---|
| Monaco diff viewer in the PR detail view | Code signing + notarization (paid Mac App distribution) |
| Auto-update via electron-updater | Second pass: correctness |
| OS keychain token storage (Electron safeStorage) | Third pass: security |
| In-app sidecar config editing | Adaptive pass routing |
| Per-repo conventions file | Prompt caching for ~20% input cost reduction |
| Packaged binaries (.dmg / .exe / .AppImage) | Webhook deployment as an alternative to the desktop app |

The architecture (see `ARCHITECTURE.md`) is designed so each lands as a contained change, not a rewrite.

---

## Development

```bash
cd sidecar
uv run pytest          # all tests, offline, ~0.5s
uv run ruff check src/ tests/
```

See [`sidecar/README.md`](./sidecar/README.md) for backend dev details and [`app/README.md`](./app/README.md) for the desktop app's planned shape.
