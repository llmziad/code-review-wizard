"""End-to-end pipeline — assembled bundle in, ReviewReport out.

We bypass Stages 1 and 2 by:
- constructing a ContextBundle directly (no GitHub fetch);
- monkey-patching `analysis.router.pick_passes` to return a stub pass that
  emits a known list of ReviewComments without calling the LLM.

This exercises the orchestration, filtering, orphan splitting, cost aggregation,
and report shaping — every Stage 3/4 concern — without an API key, a network
call, or a tree-sitter parse. Live integration is verified by the actual demo
runs that produced the fixtures, not by CI.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from reviewer.analysis.base import AnalysisPass
from reviewer.config import Settings
from reviewer.models import (
    ChangedSymbol,
    ContextBundle,
    PassResult,
    PRMetadata,
    ReviewComment,
    SymbolContext,
)
from reviewer.pipeline import review_pr


class _StubPass(AnalysisPass):
    """Fixed-comment pass for deterministic pipeline testing."""

    name = "stub"

    def __init__(self, comments: list[ReviewComment]) -> None:
        self._comments = comments

    def should_run(self, bundle: ContextBundle) -> bool:
        return True

    async def run(self, bundle: ContextBundle) -> PassResult:
        return PassResult(
            pass_name=self.name,
            comments=self._comments,
            tokens_used_input=1000,
            tokens_used_output=200,
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
        full_diff="diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -10,1 +10,1 @@\n+pass\n",
        diff_line_anchors={"x.py": [10, 11, 12]},
    )


def _comment(
    line: int, conf: float, severity: str = "suggestion", category: str = "maintainability"
) -> ReviewComment:
    return ReviewComment(
        file_path="x.py", line=line,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        title=f"t{line}", body=f"b{line}", confidence=conf, pass_name="stub",
    )


@pytest.fixture
def patch_pipeline(monkeypatch: pytest.MonkeyPatch):
    """Inject a stub pass + a stub assemble so review_pr never touches GitHub/Anthropic."""
    bundle = _bundle()
    # Different categories on L10 and L11 keep them in separate dedup buckets,
    # so we can observe two inline survivors + one orphan in the same test.
    stub_comments = [
        _comment(line=10, conf=0.95, severity="blocking", category="structure"),
        _comment(line=11, conf=0.80, severity="suggestion", category="readability"),
        _comment(line=12, conf=0.40, severity="nit", category="maintainability"),  # conf drop
        _comment(line=999, conf=0.90, severity="suggestion", category="maintainability"),  # orphan
    ]

    monkeypatch.setattr("reviewer.pipeline.assemble", lambda url, gh: bundle)
    monkeypatch.setattr(
        "reviewer.analysis.router.pick_passes",
        lambda b, s=None: [_StubPass(stub_comments)],
    )
    return bundle


def test_pipeline_produces_inline_and_orphan_split(tmp_path: Path, patch_pipeline) -> None:
    settings = Settings(
        cache_dir=tmp_path,
        min_confidence=0.7,
        max_blocking_comments=10,
        max_suggestion_comments=10,
        max_nit_comments=10,
    )
    report = asyncio.run(review_pr("https://example.com/pr/1", gh=None, settings=settings))  # type: ignore[arg-type]

    # 3 survived confidence; the 0.40 nit was dropped
    assert report.total_candidates == 4
    assert report.total_posted == 3

    # 2 inline (line 10, 11) and 1 orphan (line 999)
    inline_lines = sorted(c.line for c in report.comments)
    assert inline_lines == [10, 11]
    assert len(report.orphan_comments) == 1
    assert report.orphan_comments[0].line == 999


def test_pipeline_records_cost(tmp_path: Path, patch_pipeline) -> None:
    settings = Settings(cache_dir=tmp_path, anthropic_model="claude-sonnet-4-6")
    report = asyncio.run(review_pr("https://example.com/pr/1", gh=None, settings=settings))  # type: ignore[arg-type]

    cs = report.cost_summary
    assert cs.total_input_tokens == 1000
    assert cs.total_output_tokens == 200
    # Sonnet 4.6: $3/M input + $15/M output → (1000*3 + 200*15) / 1e6 = $0.006
    assert cs.total_cost_usd == pytest.approx(0.006, rel=1e-6)
    assert "stub" in cs.by_pass


def test_pipeline_persists_run_artifacts(tmp_path: Path, patch_pipeline) -> None:
    settings = Settings(cache_dir=tmp_path, min_confidence=0.7)
    asyncio.run(review_pr("https://example.com/pr/1", gh=None, settings=settings))  # type: ignore[arg-type]

    runs = list((tmp_path / "runs").iterdir())
    assert len(runs) == 1
    run_dir = runs[0]
    assert (run_dir / "context.json").exists()
    assert (run_dir / "passes" / "stub.json").exists()
    assert (run_dir / "dropped.json").exists()
    assert (run_dir / "report.json").exists()
