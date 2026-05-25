"""Triage operations — post_subset filters + dismiss writes the right log shape."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from reviewer.config import Settings
from reviewer.models import (
    CostSummary,
    PRMetadata,
    ReviewComment,
    ReviewReport,
)
from reviewer.triage import RunNotFoundError, dismiss, load_report, post_subset, run_dir


def _pr() -> PRMetadata:
    return PRMetadata(
        owner="o", repo="r", number=1, title="t", author="u",
        base_sha="abc1234", head_sha="def5678",
        url="https://github.com/o/r/pull/1",
    )


def _comment(comment_id: str, line: int = 10) -> ReviewComment:
    return ReviewComment(
        id=comment_id,
        file_path="x.py", line=line, category="maintainability", severity="suggestion",
        title=f"t-{comment_id}", body=f"b-{comment_id}", confidence=0.9, pass_name="stub",
    )


@pytest.fixture
def persisted_run(tmp_path: Path) -> tuple[Settings, str]:
    """Write a complete report.json + dropped.json under a fake run_id."""
    settings = Settings(cache_dir=tmp_path)
    run_id = "20260525-000000-deadbeef"
    rd = run_dir(settings, run_id)
    (rd / "passes").mkdir(parents=True, exist_ok=True)

    report = ReviewReport(
        pr=_pr(),
        comments=[_comment("a", 10), _comment("b", 11)],
        orphan_comments=[_comment("c", 999)],
        passes_run=["stub"],
        total_candidates=3,
        total_posted=3,
        cost_summary=CostSummary(),
        run_id=run_id,
    )
    (rd / "report.json").write_text(report.model_dump_json(indent=2))
    (rd / "dropped.json").write_text(json.dumps({"dropped_by_confidence": []}))
    return settings, run_id


def test_load_report_round_trips_through_disk(persisted_run) -> None:
    settings, run_id = persisted_run
    report = load_report(settings, run_id)
    assert report.run_id == run_id
    assert [c.id for c in report.comments] == ["a", "b"]
    assert [c.id for c in report.orphan_comments] == ["c"]


def test_load_report_raises_for_missing_run(tmp_path: Path) -> None:
    settings = Settings(cache_dir=tmp_path)
    with pytest.raises(RunNotFoundError):
        load_report(settings, "no-such-run")


def test_post_subset_filters_to_selected_ids(persisted_run, monkeypatch) -> None:
    """post_subset should pass only the requested comments to github_poster.post."""
    settings, run_id = persisted_run
    posted_reports: list[ReviewReport] = []

    def fake_post(gh, report):
        posted_reports.append(report)
        return "https://github.com/o/r/pull/1#pullrequestreview-123"

    monkeypatch.setattr("reviewer.triage.github_poster.post", fake_post)

    url = post_subset(gh=None, settings=settings, run_id=run_id, comment_ids=["a", "c"])
    assert url.endswith("pullrequestreview-123")
    assert len(posted_reports) == 1
    posted = posted_reports[0]
    assert [c.id for c in posted.comments] == ["a"]      # b filtered out
    assert [c.id for c in posted.orphan_comments] == ["c"]
    assert posted.total_posted == 2  # 1 inline + 1 orphan


def test_post_subset_applies_edits(persisted_run, monkeypatch) -> None:
    settings, run_id = persisted_run
    captured: list[ReviewReport] = []
    monkeypatch.setattr("reviewer.triage.github_poster.post",
                        lambda gh, r: captured.append(r) or "url")

    post_subset(gh=None, settings=settings, run_id=run_id,
                comment_ids=["a"], edits={"a": "user-edited body"})
    assert captured[0].comments[0].body == "user-edited body"


def test_post_subset_no_filter_posts_everything(persisted_run, monkeypatch) -> None:
    settings, run_id = persisted_run
    captured: list[ReviewReport] = []
    monkeypatch.setattr("reviewer.triage.github_poster.post",
                        lambda gh, r: captured.append(r) or "url")

    post_subset(gh=None, settings=settings, run_id=run_id, comment_ids=None)
    assert [c.id for c in captured[0].comments] == ["a", "b"]
    assert [c.id for c in captured[0].orphan_comments] == ["c"]


def test_dismiss_writes_user_dismissed_entries(persisted_run) -> None:
    settings, run_id = persisted_run
    count = dismiss(settings, run_id, comment_ids=["b", "c"], reason="not relevant")
    assert count == 2

    dropped = json.loads((run_dir(settings, run_id) / "dropped.json").read_text())
    # Filter-drop slots are preserved
    assert "dropped_by_confidence" in dropped
    # New user-dismissal section recorded both comments
    assert "dropped_by_user" in dropped
    user_drops = dropped["dropped_by_user"]
    assert len(user_drops) == 2
    assert {entry["id"] for entry in user_drops} == {"b", "c"}
    assert all(entry["reason"] == "not relevant" for entry in user_drops)
    assert all("dismissed_at" in entry for entry in user_drops)
    assert all("comment" in entry for entry in user_drops)


def test_dismiss_skips_unknown_ids(persisted_run) -> None:
    settings, run_id = persisted_run
    count = dismiss(settings, run_id, comment_ids=["nope"], reason="x")
    assert count == 0


def test_dismiss_appends_across_multiple_calls(persisted_run) -> None:
    settings, run_id = persisted_run
    dismiss(settings, run_id, ["a"], reason="first")
    dismiss(settings, run_id, ["b"], reason="second")
    dropped = json.loads((run_dir(settings, run_id) / "dropped.json").read_text())
    ids = [entry["id"] for entry in dropped["dropped_by_user"]]
    assert ids == ["a", "b"]
