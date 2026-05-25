"""Streaming pipeline — verifies the event sequence and the final report.

Same monkey-patching pattern as test_pipeline.py (stub assemble + stub pass)
so the test never touches the network or the LLM. We watch the event stream
to confirm the renderer-facing contract: each stage emits started/completed,
analysis emits one pass_started/pass_completed pair per pass, and the final
yield is a ReviewReport.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from reviewer.analysis.base import AnalysisPass
from reviewer.config import Settings
from reviewer.models import (
    ChangedSymbol,
    ContextBundle,
    PassResult,
    PipelineEvent,
    PRMetadata,
    ReviewComment,
    ReviewReport,
    SymbolContext,
)
from reviewer.pipeline import review_pr_stream


class _StubPass(AnalysisPass):
    name = "stub"

    def __init__(self, comments: list[ReviewComment]) -> None:
        self._comments = comments

    def should_run(self, bundle: ContextBundle) -> bool:
        return True

    async def run(self, bundle: ContextBundle) -> PassResult:
        return PassResult(
            pass_name=self.name,
            comments=self._comments,
            tokens_used_input=1234,
            tokens_used_output=567,
            duration_ms=42,
        )


def _bundle() -> ContextBundle:
    pr = PRMetadata(
        owner="o", repo="r", number=1, title="t", author="u",
        base_sha="abc1234", head_sha="def5678",
        url="https://github.com/o/r/pull/1",
    )
    sym = ChangedSymbol(
        file_path="x.py", symbol_name="foo", symbol_kind="function",
        start_line=10, end_line=20, new_source="def foo(): pass",
        language="python",
    )
    return ContextBundle(
        pr=pr,
        changed_symbols=[SymbolContext(symbol=sym)],
        repo_conventions_snippet="(stub)",
        diff_summary="1 file · 1 symbol",
        full_diff="diff --git a/x.py b/x.py\n",
        diff_line_anchors={"x.py": [10, 11, 12]},
    )


def _comment(line: int, conf: float = 0.9, category: str = "maintainability") -> ReviewComment:
    return ReviewComment(
        file_path="x.py", line=line,
        category=category,  # type: ignore[arg-type]
        severity="suggestion", title=f"t{line}", body=f"b{line}",
        confidence=conf, pass_name="stub",
    )


@pytest.fixture
def patched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Stub out all I/O so the stream can run offline."""
    bundle = _bundle()
    settings = Settings(cache_dir=tmp_path, min_confidence=0.5)

    # Replace the substeps the pipeline calls directly.
    monkeypatch.setattr("reviewer.pipeline.parse_diff", lambda diff, repo: ([], {"x.py": [10, 11]}))
    monkeypatch.setattr("reviewer.pipeline.enrich_all", lambda symbols, repo: bundle.changed_symbols)
    monkeypatch.setattr("reviewer.pipeline.get_conventions_snippet", lambda contexts: bundle.repo_conventions_snippet)

    # The GitHubClient is replaced wholesale by a stub object with the methods called.
    class _FakeGH:
        def fetch_pr_metadata(self, url):
            return bundle.pr

        def fetch_diff(self, pr):
            return bundle.full_diff

        def clone_repo(self, pr):
            return tmp_path / "repo"

    # Different categories so dedup doesn't collapse the L10/L11 pair.
    monkeypatch.setattr(
        "reviewer.analysis.router.pick_passes",
        lambda b, s=None: [_StubPass([
            _comment(10, category="structure"),
            _comment(11, conf=0.95, category="readability"),
        ])],
    )
    return settings, _FakeGH()


async def test_stream_emits_stage_boundaries_in_order(patched) -> None:
    settings, gh = patched
    events: list[PipelineEvent] = []
    report: ReviewReport | None = None
    async for item in review_pr_stream("https://example.com/pr/1", gh, settings):
        if isinstance(item, ReviewReport):
            report = item
        else:
            events.append(item)

    # The event sequence must include each stage's started + completed in order.
    stages_seen = [(e.stage, e.event) for e in events]
    assert ("context", "started") in stages_seen
    assert ("context", "completed") in stages_seen
    assert ("analysis", "started") in stages_seen
    assert ("analysis", "pass_started") in stages_seen
    assert ("analysis", "pass_completed") in stages_seen
    assert ("analysis", "completed") in stages_seen
    assert ("filtering", "started") in stages_seen
    assert ("filtering", "completed") in stages_seen

    # Ordering invariant: context completes before analysis starts, etc.
    def first_index(target):
        return next(i for i, e in enumerate(events) if (e.stage, e.event) == target)

    assert first_index(("context", "completed")) < first_index(("analysis", "started"))
    assert first_index(("analysis", "completed")) < first_index(("filtering", "started"))

    # Pass-level event carries tokens + duration
    pass_completed = next(e for e in events if e.event == "pass_completed")
    assert pass_completed.name == "stub"
    assert pass_completed.tokens_input == 1234
    assert pass_completed.tokens_output == 567
    assert pass_completed.duration_ms == 42

    # Final yield must be a ReviewReport with the run_id populated
    assert report is not None
    assert report.run_id is not None
    assert report.total_candidates == 2
    assert report.total_posted == 2


async def test_stream_progress_events_carry_human_detail(patched) -> None:
    """Progress events between stage boundaries should have a `detail` string."""
    settings, gh = patched
    progress = []
    async for item in review_pr_stream("https://example.com/pr/1", gh, settings):
        if isinstance(item, PipelineEvent) and item.event == "progress":
            progress.append(item)

    # Several within-context progress events; each has a non-empty detail
    assert len(progress) >= 3
    assert all(p.detail for p in progress)
    # And they're inside the context stage in this run (no progress events in
    # analysis or filtering yet — those are coarser)
    assert all(p.stage == "context" for p in progress)
