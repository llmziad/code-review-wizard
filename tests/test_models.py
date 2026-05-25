"""Model validators — make sure the contract refuses bad input."""

from __future__ import annotations

from datetime import UTC

import pytest
from pydantic import ValidationError

from reviewer.models import (
    ChangedSymbol,
    ContextBundle,
    PRMetadata,
    ReviewComment,
)


def _good_pr() -> PRMetadata:
    return PRMetadata(
        owner="o",
        repo="r",
        number=1,
        title="t",
        author="u",
        base_sha="abc1234",
        head_sha="def5678",
        url="https://github.com/o/r/pull/1",
    )


def test_pr_metadata_rejects_non_sha() -> None:
    with pytest.raises(ValidationError, match="not a git SHA"):
        PRMetadata(
            owner="o", repo="r", number=1, title="t", author="u",
            base_sha="not-a-sha", head_sha="def5678",
            url="https://github.com/o/r/pull/1",
        )


def test_pr_metadata_requires_positive_number() -> None:
    with pytest.raises(ValidationError):
        PRMetadata(
            owner="o", repo="r", number=0, title="t", author="u",
            base_sha="abc1234", head_sha="def5678",
            url="https://github.com/o/r/pull/1",
        )


def test_changed_symbol_rejects_end_before_start() -> None:
    with pytest.raises(ValidationError, match=">= start_line"):
        ChangedSymbol(
            file_path="x.py",
            symbol_name="foo",
            symbol_kind="function",
            start_line=10,
            end_line=5,
            new_source="def foo(): pass",
            language="python",
        )


def test_review_comment_confidence_bounds() -> None:
    for bad in (-0.1, 1.1, 2.0):
        with pytest.raises(ValidationError):
            ReviewComment(
                file_path="x.py", line=1, category="readability", severity="nit",
                title="t", body="b", confidence=bad, pass_name="p",
            )


def test_review_comment_forbids_extra_fields() -> None:
    """`extra=forbid` is the LLM tool-use contract guard."""
    with pytest.raises(ValidationError):
        ReviewComment(
            file_path="x.py", line=1, category="readability", severity="nit",
            title="t", body="b", confidence=0.5, pass_name="p",
            extra_field="nope",  # type: ignore[call-arg]
        )


def test_pr_summary_accepts_minimal_search_shape() -> None:
    from datetime import datetime

    from reviewer.models import PRSummary

    s = PRSummary(
        url="https://github.com/o/r/pull/1",
        owner="o",
        repo="r",
        number=1,
        title="t",
        author="u",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    assert s.state == "open"
    assert s.draft is False


def test_pr_summary_rejects_invalid_number() -> None:
    from datetime import datetime

    from reviewer.models import PRSummary

    with pytest.raises(ValidationError):
        PRSummary(
            url="x", owner="o", repo="r", number=0, title="t", author="u",
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 2, tzinfo=UTC),
        )


def test_context_bundle_valid_lines_helper() -> None:
    bundle = ContextBundle(
        pr=_good_pr(),
        changed_symbols=[],
        repo_conventions_snippet="",
        diff_summary="",
        diff_line_anchors={"x.py": [10, 11, 12]},
    )
    assert bundle.valid_lines_for("x.py") == {10, 11, 12}
    assert bundle.valid_lines_for("missing.py") == set()
