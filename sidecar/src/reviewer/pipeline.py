"""End-to-end orchestration — Stage 1 → 2 → 3 → ReviewReport, with streaming events.

Two entry points:
    review_pr_stream(...) -> AsyncIterator[PipelineEvent | ReviewReport]
        Yields PipelineEvent objects at every meaningful step; final yield is
        the ReviewReport. This is what the JSON-RPC sidecar surfaces to the
        desktop UI for real-time progress.

    review_pr(...) -> ReviewReport
        Thin wrapper that consumes the stream and returns the final report.
        For callers that don't want progress events (the CLI's --json output,
        tests, etc.).

Per-run persistence under `cache/runs/<run_id>/`:
    context.json   — the assembled ContextBundle
    passes/<name>.json — one PassResult per pass
    dropped.json   — every comment dropped by filtering, with the stage that dropped it
    report.json    — the final ReviewReport

These artifacts are the substrate for the (future) feedback loop: pair them
with GitHub's resolve/dismiss signals to tune confidence / dedup / caps
against real signal.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

from .config import Settings
from .context.conventions import get_conventions_snippet
from .context.diff_parser import parse as parse_diff
from .context.symbol_resolver import enrich_all
from .github_client import GitHubClient
from .models import (
    ContextBundle,
    CostSummary,
    PassResult,
    PipelineEvent,
    ReviewComment,
    ReviewReport,
)

# Approximate token pricing (USD per 1M tokens). Tracks current Anthropic
# Sonnet/Opus/Haiku list pricing; overrides could move to config if accuracy
# ever matters at finer granularity.
_PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-sonnet-4-5": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-opus-4-6": (15.0, 75.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    in_rate, out_rate = _PRICING_PER_MTOK.get(model, (3.0, 15.0))
    return (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000


def new_run_dir(settings: Settings) -> Path:
    """Create and return a fresh run directory. The basename is the run_id."""
    settings.ensure_cache_dirs()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = settings.cache_dir / "runs" / run_id
    (run_dir / "passes").mkdir(parents=True, exist_ok=True)
    return run_dir


# ---------------------------------------------------------------------------
# Streaming pipeline (the canonical entry point)
# ---------------------------------------------------------------------------


async def review_pr_stream(
    url: str,
    gh: GitHubClient,
    settings: Settings,
    run_dir: Path | None = None,
) -> AsyncIterator[PipelineEvent | ReviewReport]:
    """Run the full pipeline, yielding progress events then the final report.

    The desktop UI watches this stream to render live progress. The CLI's
    non-streaming `review_pr` wraps this and returns the report.
    """
    from .analysis.router import pick_passes  # local: keeps CLI import light

    run_dir = run_dir or new_run_dir(settings)
    run_id = run_dir.name

    # ---- Stage 1: context (inlined from assemble() so we can yield progress) ----
    yield PipelineEvent(stage="context", event="started")

    yield PipelineEvent(stage="context", event="progress", detail="Fetching PR metadata")
    pr = gh.fetch_pr_metadata(url)

    yield PipelineEvent(
        stage="context",
        event="progress",
        detail=f"Fetching diff for {pr.owner}/{pr.repo}#{pr.number}",
    )
    diff = gh.fetch_diff(pr)

    yield PipelineEvent(stage="context", event="progress", detail="Cloning repo (shallow)")
    repo = gh.clone_repo(pr)

    yield PipelineEvent(stage="context", event="progress", detail="Parsing diff + extracting symbols")
    symbols, anchors = parse_diff(diff, repo)

    yield PipelineEvent(
        stage="context",
        event="progress",
        detail=f"Resolving callers + siblings for {len(symbols)} symbol(s)",
    )
    contexts = enrich_all(symbols, repo)

    conventions = get_conventions_snippet(contexts)
    bundle = ContextBundle(
        pr=pr,
        changed_symbols=contexts,
        repo_conventions_snippet=conventions,
        diff_summary=_diff_summary(contexts, anchors),
        full_diff=diff,
        diff_line_anchors=anchors,
    )
    (run_dir / "context.json").write_text(bundle.model_dump_json(indent=2))
    yield PipelineEvent(stage="context", event="completed", detail=bundle.diff_summary)

    # ---- Stage 2: analysis ----
    yield PipelineEvent(stage="analysis", event="started")
    passes = pick_passes(bundle, settings)
    pass_results: list[PassResult] = []
    skipped: list[str] = []

    for pass_ in passes:
        if not pass_.should_run(bundle):
            skipped.append(pass_.name)
            continue
        yield PipelineEvent(stage="analysis", event="pass_started", name=pass_.name)
        result = await pass_.run(bundle)
        pass_results.append(result)
        (run_dir / "passes" / f"{pass_.name}.json").write_text(result.model_dump_json(indent=2))
        yield PipelineEvent(
            stage="analysis",
            event="pass_completed",
            name=pass_.name,
            tokens_input=result.tokens_used_input,
            tokens_output=result.tokens_used_output,
            duration_ms=result.duration_ms,
            detail=(
                f"{len(result.comments)} candidate comment(s); error: {result.error}"
                if result.error
                else f"{len(result.comments)} candidate comment(s)"
            ),
        )
    yield PipelineEvent(stage="analysis", event="completed")

    all_candidates: list[ReviewComment] = [c for r in pass_results for c in r.comments]

    # ---- Stage 3: filtering ----
    yield PipelineEvent(stage="filtering", event="started")
    from .filtering import apply_all  # local: filtering is cheap to import but keep symmetric
    survivors, dropped = apply_all(all_candidates, settings)
    (run_dir / "dropped.json").write_text(
        json.dumps(
            {stage: [c.model_dump() for c in lst] for stage, lst in dropped.items()},
            indent=2,
        )
    )
    inline, orphan = _split_by_anchor(survivors, bundle)
    yield PipelineEvent(
        stage="filtering",
        event="completed",
        detail=(
            f"{len(survivors)} kept of {len(all_candidates)} candidates · "
            f"{len(inline)} inline, {len(orphan)} orphan"
        ),
    )

    # ---- Final report ----
    cost = _aggregate_cost(pass_results, settings.anthropic_model)
    report = ReviewReport(
        pr=bundle.pr,
        comments=inline,
        orphan_comments=orphan,
        summary=_default_summary(pass_results, bundle, len(inline) + len(orphan)),
        passes_run=[r.pass_name for r in pass_results],
        passes_skipped=skipped,
        total_candidates=len(all_candidates),
        total_posted=len(inline) + len(orphan),
        cost_summary=cost,
        run_id=run_id,
    )
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    yield report


# ---------------------------------------------------------------------------
# Non-streaming wrapper (existing callers keep working)
# ---------------------------------------------------------------------------


async def review_pr(
    url: str, gh: GitHubClient, settings: Settings, run_dir: Path | None = None
) -> ReviewReport:
    """Consume the streaming pipeline and return only the final ReviewReport."""
    report: ReviewReport | None = None
    async for item in review_pr_stream(url, gh, settings, run_dir):
        if isinstance(item, ReviewReport):
            report = item
    if report is None:  # defensive: stream must yield a report or raise
        raise RuntimeError("Pipeline did not yield a final ReviewReport")
    return report


def run_review_sync(url: str, gh: GitHubClient, settings: Settings) -> ReviewReport:
    """Sync wrapper for the Typer command (Typer doesn't await directly)."""
    return asyncio.run(review_pr(url, gh, settings))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _diff_summary(contexts, anchors) -> str:
    files = len(anchors)
    syms = len(contexts)
    lines = sum(len(v) for v in anchors.values())
    return f"{files} file(s) · {syms} changed symbol(s) · {lines} anchorable line(s)"


def _split_by_anchor(
    comments: list[ReviewComment], bundle: ContextBundle
) -> tuple[list[ReviewComment], list[ReviewComment]]:
    inline: list[ReviewComment] = []
    orphan: list[ReviewComment] = []
    for c in comments:
        if c.line in bundle.valid_lines_for(c.file_path):
            inline.append(c)
        else:
            orphan.append(c)
    return inline, orphan


def _aggregate_cost(results: list[PassResult], model: str) -> CostSummary:
    total_in = sum(r.tokens_used_input for r in results)
    total_out = sum(r.tokens_used_output for r in results)
    by_pass: dict[str, dict[str, float]] = {}
    for r in results:
        cost = estimate_cost(model, r.tokens_used_input, r.tokens_used_output)
        by_pass[r.pass_name] = {
            "input_tokens": float(r.tokens_used_input),
            "output_tokens": float(r.tokens_used_output),
            "cost_usd": cost,
        }
    return CostSummary(
        total_input_tokens=total_in,
        total_output_tokens=total_out,
        total_cost_usd=estimate_cost(model, total_in, total_out),
        by_pass=by_pass,
    )


def _default_summary(passes: list[PassResult], bundle: ContextBundle, posted: int) -> str:
    pass_names = ", ".join(r.pass_name for r in passes) or "(no passes run)"
    return (
        f"Code Review Wizard · {bundle.diff_summary}\n"
        f"Passes: {pass_names}\n"
        f"Surfaced {posted} comment(s) after filtering."
    )
