"""End-to-end orchestration — Stage 1 → 2 → 3 → ReviewReport.

The CLI commands stay thin by delegating here. The split:

    cli.py           — Typer surface, argument parsing, console wiring
    pipeline.py      — assemble → analyze → filter → report
    context/, analysis/, filtering/, delivery/  — the four stages

Each run lands a JSON record under `cache/runs/<run_id>/`:
    context.json   — the assembled ContextBundle
    passes/<name>.json — one PassResult per pass
    dropped.json   — every comment dropped by filtering, with the stage that dropped it
    report.json    — the final ReviewReport (what gets rendered / posted)

This isn't just debug paranoia. It's the data substrate for the (future)
feedback loop: pair these records with GitHub's resolve/dismiss signals over
time and you can tune the confidence threshold, the dedup window, and the
per-severity caps against real signal.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from .config import Settings
from .context.assembler import assemble
from .filtering import apply_all
from .github_client import GitHubClient
from .models import (
    ContextBundle,
    CostSummary,
    PassResult,
    ReviewComment,
    ReviewReport,
)

# Approximate token pricing (USD per 1M tokens). Tracks current Anthropic
# Sonnet/Opus/Haiku list pricing closely enough for a take-home cost summary;
# overrides could move to config if accuracy ever matters.
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
    settings.ensure_cache_dirs()
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    run_dir = settings.cache_dir / "runs" / run_id
    (run_dir / "passes").mkdir(parents=True, exist_ok=True)
    return run_dir


async def review_pr(
    url: str, gh: GitHubClient, settings: Settings, run_dir: Path | None = None
) -> ReviewReport:
    """Full pipeline from PR URL to ReviewReport, persisting artifacts en route."""
    from .analysis.router import pick_passes  # local import: keeps CLI import light

    run_dir = run_dir or new_run_dir(settings)

    # ---- Stage 1: context ----
    bundle = assemble(url, gh)
    (run_dir / "context.json").write_text(bundle.model_dump_json(indent=2))

    # ---- Stage 2: analysis ----
    passes = pick_passes(bundle, settings)
    pass_results: list[PassResult] = []
    skipped: list[str] = []

    for pass_ in passes:
        if not pass_.should_run(bundle):
            skipped.append(pass_.name)
            continue
        result = await pass_.run(bundle)
        pass_results.append(result)
        (run_dir / "passes" / f"{pass_.name}.json").write_text(result.model_dump_json(indent=2))

    all_candidates: list[ReviewComment] = [c for r in pass_results for c in r.comments]

    # ---- Stage 3: filtering ----
    survivors, dropped = apply_all(all_candidates, settings)
    (run_dir / "dropped.json").write_text(
        json.dumps(
            {stage: [c.model_dump() for c in lst] for stage, lst in dropped.items()},
            indent=2,
        )
    )

    # Split surviving comments into inline-anchored vs orphan
    inline, orphan = _split_by_anchor(survivors, bundle)

    # ---- Stage 4 substrate: cost + report ----
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
    )
    (run_dir / "report.json").write_text(report.model_dump_json(indent=2))
    return report


def run_review_sync(url: str, gh: GitHubClient, settings: Settings) -> ReviewReport:
    """Thin sync wrapper for the Typer command (Typer doesn't await directly)."""
    return asyncio.run(review_pr(url, gh, settings))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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
        f"Smart Code Reviewer · {bundle.diff_summary}\n"
        f"Passes: {pass_names}\n"
        f"Surfaced {posted} comment(s) after filtering."
    )
