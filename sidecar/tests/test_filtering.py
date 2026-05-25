"""Filtering pipeline — synthetic comments through confidence + dedup + cap."""

from __future__ import annotations

from reviewer.config import Settings
from reviewer.filtering import apply_all, dedupe_by_location, filter_by_confidence, prioritize
from reviewer.models import ReviewComment


def mk(file: str, line: int, severity: str, category: str, conf: float) -> ReviewComment:
    return ReviewComment(
        file_path=file,
        line=line,
        category=category,  # type: ignore[arg-type]
        severity=severity,  # type: ignore[arg-type]
        title=f"t{line}",
        body=f"b{line}",
        confidence=conf,
        pass_name="test",
    )


def test_confidence_filter_keeps_at_or_above_threshold() -> None:
    comments = [
        mk("a.py", 1, "suggestion", "readability", 0.5),
        mk("a.py", 2, "suggestion", "readability", 0.7),
        mk("a.py", 3, "suggestion", "readability", 0.9),
    ]
    kept, dropped = filter_by_confidence(comments, threshold=0.7)
    assert [c.line for c in kept] == [2, 3]
    assert [c.line for c in dropped] == [1]


def test_dedup_keeps_highest_confidence_per_cluster() -> None:
    """Two comments on lines 10 and 11 (same file, same category) are one cluster."""
    comments = [
        mk("a.py", 10, "blocking", "structure", 0.80),
        mk("a.py", 11, "blocking", "structure", 0.95),  # winner
        mk("a.py", 12, "blocking", "structure", 0.70),  # also in 10/5 == 11/5 bucket
        mk("b.py", 10, "blocking", "structure", 0.85),  # different file, kept
    ]
    kept, dropped = dedupe_by_location(comments)
    kept_keys = sorted((c.file_path, c.line) for c in kept)
    assert kept_keys == [("a.py", 11), ("b.py", 10)]
    assert len(dropped) == 2


def test_dedup_respects_category_in_cluster_key() -> None:
    """Same line, different category → not a duplicate."""
    comments = [
        mk("a.py", 50, "suggestion", "readability", 0.80),
        mk("a.py", 50, "suggestion", "maintainability", 0.85),
    ]
    kept, dropped = dedupe_by_location(comments)
    assert len(kept) == 2
    assert len(dropped) == 0


def test_prioritize_orders_by_severity_then_confidence_then_caps() -> None:
    settings = Settings(
        max_blocking_comments=1,
        max_suggestion_comments=2,
        max_nit_comments=1,
    )
    comments = [
        mk("a.py", 1, "nit", "readability", 0.95),
        mk("a.py", 2, "nit", "readability", 0.80),     # dropped: nit cap = 1
        mk("a.py", 3, "blocking", "structure", 0.70),
        mk("a.py", 4, "blocking", "structure", 0.95),  # dropped: blocking cap = 1
        mk("a.py", 5, "suggestion", "maintainability", 0.90),
        mk("a.py", 6, "suggestion", "maintainability", 0.75),
        mk("a.py", 7, "suggestion", "maintainability", 0.85),  # dropped: suggestion cap = 2
    ]
    kept, dropped = prioritize(comments, settings)
    severities = [c.severity for c in kept]
    # Sorted by severity rank (blocking < suggestion < nit), then by -confidence
    assert severities == ["blocking", "suggestion", "suggestion", "nit"]
    # The kept blocking is the one with the higher confidence
    assert kept[0].confidence == 0.95
    # The two kept suggestions are the higher-confidence pair
    assert sorted(c.confidence for c in kept if c.severity == "suggestion") == [0.85, 0.90]
    assert len(dropped) == 3


def test_apply_all_runs_full_pipeline_and_returns_per_stage_drops() -> None:
    settings = Settings(
        min_confidence=0.7,
        max_blocking_comments=10,
        max_suggestion_comments=10,
        max_nit_comments=10,
    )
    comments = [
        mk("a.py", 1, "blocking", "structure", 0.30),       # confidence drop
        mk("a.py", 50, "suggestion", "readability", 0.80),
        mk("a.py", 51, "suggestion", "readability", 0.85),  # dedup victim (50/5 == 51/5)
        mk("b.py", 100, "nit", "maintainability", 0.95),
    ]
    survivors, dropped = apply_all(comments, settings)

    assert len(dropped["dropped_by_confidence"]) == 1
    assert len(dropped["dropped_by_dedup"]) == 1
    assert len(dropped["dropped_by_priority"]) == 0
    assert len(survivors) == 2
    # The kept suggestion is the higher-confidence one
    suggestion = next(c for c in survivors if c.severity == "suggestion")
    assert suggestion.confidence == 0.85
