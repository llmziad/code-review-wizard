# Architecture

## 1. Problem framing — why naive PR review with an LLM fails

The first instinct on this problem is also the wrong one: take the diff, paste
it into a system prompt, ask the model to "review this code." The result is a
review the team will mute within a week.

Four things separate a useful review from that:

1. **Codebase conventions.** "Use a `Result` type" is good advice in a codebase
   that has them, and noise in one that doesn't. A reviewer who doesn't know
   the conventions can only ever surface style platitudes.
2. **Change intent.** What was the author trying to do? The PR title and body
   are evidence; so is the surrounding test that landed in the same PR.
3. **Blast radius.** Is the changed function called from five places or fifty?
   A "minor refactor" can be a load-bearing API.
4. **Signal vs. noise.** Even a great review of one function is muted if it
   arrives with twelve nits attached. The hardest part of automated review
   isn't generating comments; it's deciding which ones to send.

Each of those is a separate concern. We give each one its own stage.

## 2. The four-stage pipeline

```
  GitHub PR URL
       │
       ▼
┌─────────────────┐    Stage 1 — Context assembly
│ context/        │    PR metadata, AST-aware symbol extraction,
│   assembler.py  │    caller resolution (ripgrep + AST), sibling
│   diff_parser   │    enumeration, repo-conventions snippet.
│   symbol_       │    Output: ContextBundle.
│     resolver    │    Heuristic > optimal: this stage hits the disk,
│   conventions   │    not the GitHub API, for symbol search.
└─────────────────┘
       │
       ▼
┌─────────────────┐    Stage 2 — Analysis
│ analysis/       │    Router picks passes for this bundle (MVP:
│   router        │    always [maintainability]). Each pass is one
│   base          │    LLM call with structured output via tool-use.
│   maintain-     │    Each pass is independent — adding a security
│     ability     │    or correctness pass is one file.
│   prompts/      │    Output: list[PassResult] → list[ReviewComment].
└─────────────────┘
       │
       ▼
┌─────────────────┐    Stage 3 — Filtering
│ filtering/      │    Three pure filters in sequence:
│   confidence    │      drop below MIN_CONFIDENCE
│   dedup         │      collapse near-duplicates by location
│   prioritizer   │      sort by severity+confidence, cap per-severity
│                 │    Drops are logged — feedback-loop substrate.
└─────────────────┘
       │
       ▼
┌─────────────────┐    Stage 4 — Delivery
│ delivery/       │    Terminal: rich-formatted, file-grouped panels.
│   terminal_     │    GitHub: one review (event=COMMENT), inline-anchored.
│     renderer    │    Out-of-diff comments are folded into the review
│   github_poster │    body as "general observations" instead of being
│                 │    silently dropped.
└─────────────────┘
       │
       ▼
   ReviewReport
```

Stage boundaries are honest contracts: each stage takes the previous stage's
output verbatim and emits a new model. There's a single Pydantic file
(`models.py`) that defines every cross-stage type. A model that accepts garbage
is a leak; a model that rejects garbage at the boundary keeps every downstream
stage simpler.

## 3. Key architectural decisions

### Sidecar process over per-call subprocess for the desktop app
The CLI doubles as a long-lived sidecar (`reviewer serve`). The desktop app
spawns it once at launch and exchanges line-delimited JSON requests on
stdin/stdout. The alternative — spawn a fresh CLI subprocess per call — burns
~200ms of Python startup every list refresh and makes streaming progress hard.
The sidecar pays the startup cost once, runs concurrent requests as asyncio
tasks (each tagged with `request_id` so events route correctly to the UI),
and streams real-time pipeline events back as they happen.

JSON-RPC over stdio specifically (not HTTP) because: no port conflicts, no
firewall prompts on macOS, no `launchctl`-style daemon to manage, and Electron
owns the process lifecycle naturally — kill the parent, the child dies.

### Streaming pipeline via async generator
`pipeline.review_pr_stream(...)` yields `PipelineEvent` objects at every
meaningful step (fetching metadata, cloning, parsing, each LLM pass) and
finally yields the `ReviewReport`. The CLI's non-streaming `review_pr` wraps
it for callers that don't care about progress, but the sidecar surfaces every
event to the renderer so the UI shows "Cloning repo... Resolving callers for
14 symbols... Pass maintainability completed (7,234 in / 1,200 out tokens)..."
instead of a 10-second blank wait.

**Alternative rejected:** Single `review_pr() -> ReviewReport` and let the UI
fake progress with a spinner. That works but lies — when the model takes 8s
the user has no idea if it's stuck. Real progress is honest.

### Triage as a separate stage on cached runs
Every run persists `report.json`. The desktop app's per-comment
approve/dismiss/edit triage operates on that file via three operations:
`get_run`, `post_review(comment_ids, edits)`, `dismiss_comments(ids, reason)`.
None re-run the pipeline. Each `ReviewComment` carries a stable 8-char id
(generated at construction, not by the LLM) so the renderer can address
specific comments across save/load round trips. Dismissals append to a new
`dropped_by_user` array in `dropped.json`, parallel to the existing filter-drop
sections — the feedback loop can tell the two apart.

### Symbol-level diff parsing, not line-level
A unified diff tells you "what bytes moved." A symbol diff tells you "what
behavior changed." Reviews are about behavior. Tree-sitter parses the post-
image of each changed file and maps every added line to its smallest enclosing
function/method/class. The downstream pass reasons about whole functions, not
dangling line fragments.

**Alternative rejected:** Send raw line diffs and let the LLM figure it out.
It can, often. But every prompt has to re-pay the cost of "what is this code
about," and the model has no anchor when it wants to refer to a function by
name in a comment.

### Multi-pass architecture with a single mega-prompt as the rejected alternative
Each AnalysisPass is a separate file and a separate LLM call. The router picks
which ones to run.

**Why not one mega-prompt that does everything?** Because:
- Specialized passes can use different models (maintainability → Sonnet,
  cheap-and-fast pre-filter → Haiku).
- Each pass's `confidence` is calibratable in isolation.
- Adding "security review" is one file, not a prompt rewrite.
- Passes can run in parallel (asyncio.gather) at high volume.
- A blast-radius bug in one pass can't poison another's output.

The MVP ships one pass to keep the diff small. The interface is there so the
second pass is a file, not a rewrite.

### Structured output via tool-use, not prose JSON parsing
The maintainability pass defines a `submit_review_comments` tool and calls it
with `tool_choice` set. The model returns `tool_use` blocks whose `input` is
already a parsed object. No JSON regex, no markdown-fence stripping, no
"the model decided to wrap it in code today." The `ReviewComment` Pydantic
model validates the input; a malformed comment is dropped at the boundary
instead of corrupting the run.

### File-based cache for the MVP, Postgres-shaped at the seam
Every run lands in `cache/runs/<run_id>/`:
```
context.json        the ContextBundle
passes/maintainability.json   the PassResult
dropped.json        every dropped comment, by stage
report.json         the final ReviewReport
```

These files are the substrate of the (future) feedback loop. The schema is
already Pydantic; the migration to Postgres is "wrap a SQLAlchemy session
around the same models." No business logic moves.

### In-diff anchoring is enforced in the data model, not just the poster
GitHub's review API rejects inline comments on lines that aren't part of the
diff. We carry the set of valid anchor lines per file inside `ContextBundle`
(`diff_line_anchors`) and surface it to the prompt, then split surviving
comments into `inline` and `orphan` post-filter. Orphans land in the review
summary body, not the trash. This decision lives in the model so it can't be
bypassed by a clever-but-naive caller.

### Symbol search hits the filesystem, not the GitHub API
For each PR we shallow-clone the head ref into `cache/repos/`. Caller
resolution then runs `rg --json` locally. Per-file API calls would burn rate
limit and add seconds per symbol on any real repo. The API is the boundary
for *metadata*; the disk is the boundary for *search*.

## 4. What's stubbed and why

| Component | MVP behavior | Production behavior | Why stubbed |
|---|---|---|---|
| Conventions retrieval (`context/conventions.py`) | Hardcoded language-aware string | Embedding search over `CONVENTIONS.md` + representative repo code, retrieving chunks similar to the changed symbols | Needs a continuously-updated index and an embedding model in the loop; out of scope for one take-home |
| Adaptive pass routing (`analysis/router.py`) | Always `[MaintainabilityPass]` | Tiny classifier over diff features (file types, size, change locality) chooses the subset of passes that earns their token cost | Needs training data and a labeled corpus of "which pass mattered for which PR" |
| Multi-pass orchestration | One pass, interface for more | Parallel `asyncio.gather` across passes; pass-level retries; per-pass model selection (Haiku for fast pre-filter, Sonnet for synthesis) | One pass demonstrates the interface; adding more is repetition that wouldn't earn the interview a different answer |
| Feedback loop | Drops are logged per stage to `dropped.json` | Pair drop logs with GitHub resolve/dismiss signals; tune `MIN_CONFIDENCE`, dedup window, and per-severity caps against real outcomes | Needs deployment history this project can't have |
| Convention enforcement | Snippet goes in the prompt | Convention check is its own pass that scores each comment for convention-consistency before letting it through | One pass for the take-home; the interface accepts more |
| Old-source diff | `ChangedSymbol.old_source` is always `None` | Apply the diff in reverse to reconstruct the pre-PR source of each symbol | The LLM has the unified diff in the prompt, which is enough for maintainability judgments. Real value only at the correctness pass |

Each row of this table is a designed-for extension point. The interview point:
*it would take roughly one file per row to ship it.*

## 5. Scaling notes — what changes at 10 / 100 / 1000 PRs per day

### 10/day
Anything works. Single process, file cache, sequential pipeline. Cost
(~$0.04/PR with one pass) = ~$12/month. The setup as shipped.

### 100/day
- Move pipeline into a worker queue (Celery, RQ, or Cloud Tasks).
- Add concurrency: `asyncio.gather` across passes inside one PR; multiple PRs
  in flight at once. Anthropic's rate limits and your account's tier are the
  ceiling.
- Persist runs to Postgres (the Pydantic models translate 1:1).
- Add an eval harness: fixed set of historical PRs, weekly regression run
  comparing the new prompt/filter weights against human reviewer ground truth.
- Enable Anthropic prompt caching for the system prompt + repo conventions.
  That's free-money for any pass that runs more than once per minute.

### 1000/day
- Tier the model usage: Haiku does a cheap "is this PR even worth a full
  review" pre-filter; Sonnet runs only when Haiku says yes. Likely cuts cost
  by 60–80%.
- Move from synchronous polling to webhook-driven: GitHub's `pull_request`
  webhook fires the worker.
- Redis for run state (in-flight, retry-counts) so workers can crash without
  losing visibility.
- Split the maintainability pass into chunked passes for very large PRs
  (current limit: one shot, ~12K diff chars before truncation).
- Cost at this volume = ~$1,200/month before optimization, ~$300/month after
  tiering and caching. Worth the engineering.

## 6. Cost model

Per-PR token usage on Claude Sonnet 4.6 (with maintainability pass only):

| Component | Tokens (typical) |
|---|---|
| System prompt (`maintainability.md`) | ~1,500 input |
| User message: title + description | ~500 input |
| User message: full diff | ~2,000–10,000 input |
| User message: changed symbols + callers | ~1,000–5,000 input |
| User message: anchor lists, conventions, frame | ~500 input |
| Tool output (review comments) | 200–2,000 output |
| **Total** | ~6,000–18,000 input, ~500–2,000 output |

At Sonnet 4.6 list pricing ($3/M input, $15/M output):

| PR size | Cost per PR | Cost @ 100 PR/day |
|---|---|---|
| Small (~50 LOC, 2 files) | ~$0.025 | ~$75/mo |
| Medium (~300 LOC, 10 files) | ~$0.045 | ~$135/mo |
| Large (~1000 LOC, 25 files) | ~$0.08 | ~$240/mo |

These costs drop ~20% with Anthropic prompt caching on the system prompt.

## 7. Failure modes

### The LLM is confidently wrong
A comment with `confidence: 0.95` that's nonsense. Three layers of defense:
1. **Confidence is self-scored, then filtered.** The prompt anchors confidence
   to evidence strength. The threshold is a knob — production tunes it from
   the feedback loop.
2. **Structured output via tool-use.** The model can't ship a malformed
   comment by accident; the schema rejects it.
3. **Severity caps.** Even if the model lights up about everything, at most
   3 blocking + 5 suggestion + 3 nit make it to the PR. The team isn't muting
   us based on volume.

The unaddressed failure mode is "structurally valid but factually wrong" —
e.g. the model claims a function is duplicated when it isn't. That's the
convention-check pass's job, which is the next thing to build.

### GitHub is down or rate-limited
- `fetch_pr_metadata` and `fetch_diff` fail loudly. The CLI surfaces the
  error; no half-built reviews land.
- `post_review` is the only mutating call. If it 5xxs, the run is already
  persisted to `cache/runs/<id>/report.json`; re-running with `--post` against
  the same URL is safe and idempotent (just creates a second review).

### Huge PR (10K+ LOC)
- Current behavior: the unified diff is truncated to 12K chars in the prompt
  (after which the model sees a `[truncated ...]` marker). Per-symbol
  `new_source` is capped at 3K chars. Symbol resolution still runs; the
  prompt just sees less.
- Production fix: chunk the symbols across multiple LLM calls, then merge.
  The data model already supports it (each pass returns its own
  `list[ReviewComment]`; the filter merges them).

### Fork PRs where the head repo is private/deleted
`clone_repo` falls back to the base repo URL. If the head SHA isn't reachable
from base, `git clone` fails loudly and the run aborts. The fix in production
is to grant the bot a token with access to forks, or to detect this case and
post a summary-only review (no inline anchors) using just the diff text.

### Race condition: PR head force-pushed between fetch and clone
`fetch_pr_metadata` returns the SHA at fetch time, but `git clone --branch
<head_ref>` clones whatever's at the tip *now*. If the author force-pushes
between the two, the symbols come from a different commit than the diff. The
mismatch shows up as "couldn't find file" errors during symbol extraction —
those files get a file-level pseudo-symbol fallback instead of crashing. The
production fix is to re-fetch metadata after clone and reject if SHAs differ.
