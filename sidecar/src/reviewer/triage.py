"""Triage actions on previously-run reviews.

The desktop app's triage workflow needs to operate on a *cached* run, not
re-run the pipeline:

    review_pr → render comments → user approves/dismisses/edits some →
        post_subset(...)  ships only the approved ones
        dismiss(...)      records the rest as user-rejected (feedback substrate)

Every action addresses comments by their stable `id` (set when the comment was
constructed inside the maintainability pass). Run state is persisted at
`cache/runs/<run_id>/{report.json, dropped.json}`.

All operations are pure functions over disk state — no in-memory cache, no
hidden globals. The same file `dropped.json` that the pipeline writes for
filter drops gains a new `user_dismissed` key when a user dismisses a comment.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings
from .delivery import github_poster
from .github_client import GitHubClient
from .models import ReviewComment, ReviewReport


class RunNotFoundError(LookupError):
    """Raised when a run_id doesn't have a persisted report.json."""


def run_dir(settings: Settings, run_id: str) -> Path:
    return settings.cache_dir / "runs" / run_id


def load_report(settings: Settings, run_id: str) -> ReviewReport:
    """Load a previously-persisted ReviewReport. Raises RunNotFoundError on miss."""
    path = run_dir(settings, run_id) / "report.json"
    if not path.exists():
        raise RunNotFoundError(f"No report.json under run_id={run_id!r}")
    return ReviewReport.model_validate_json(path.read_text())


# ---------------------------------------------------------------------------
# post_subset — ship only the comments the user approved
# ---------------------------------------------------------------------------


def post_subset(
    gh: GitHubClient,
    settings: Settings,
    run_id: str,
    comment_ids: list[str] | None = None,
    edits: dict[str, str] | None = None,
) -> str:
    """Post a previously-cached review, filtered to the given comment ids.

    Args:
        run_id: which cached run to post.
        comment_ids: ids of inline+orphan comments to include. None = all.
        edits: {comment_id: new_body} for any comments the user edited.

    Returns the GitHub review URL.
    """
    report = load_report(settings, run_id)
    edits = edits or {}

    keep_ids: set[str] | None = set(comment_ids) if comment_ids is not None else None

    def filter_and_edit(comments: list[ReviewComment]) -> list[ReviewComment]:
        out: list[ReviewComment] = []
        for c in comments:
            if keep_ids is not None and c.id not in keep_ids:
                continue
            if c.id in edits:
                # model_copy(update=...) returns a new instance with the body changed
                c = c.model_copy(update={"body": edits[c.id]})
            out.append(c)
        return out

    filtered_inline = filter_and_edit(report.comments)
    filtered_orphan = filter_and_edit(report.orphan_comments)

    posted = report.model_copy(update={
        "comments": filtered_inline,
        "orphan_comments": filtered_orphan,
        "total_posted": len(filtered_inline) + len(filtered_orphan),
    })
    return github_poster.post(gh, posted)


# ---------------------------------------------------------------------------
# dismiss — record user rejections for the feedback loop
# ---------------------------------------------------------------------------


def dismiss(
    settings: Settings,
    run_id: str,
    comment_ids: list[str],
    reason: str = "",
) -> int:
    """Append user-dismissals to dropped.json. Returns the number recorded.

    The drop log already has stages like `dropped_by_confidence` for filter
    drops. We add a parallel `dropped_by_user` array so the feedback loop
    can distinguish user-rejected from filter-rejected comments — both are
    "drop" signals but they mean different things for tuning.
    """
    report = load_report(settings, run_id)

    by_id = {c.id: c for c in report.comments + report.orphan_comments}
    to_record = [by_id[cid] for cid in comment_ids if cid in by_id]
    if not to_record:
        return 0

    dropped_path = run_dir(settings, run_id) / "dropped.json"
    payload: dict = {}
    if dropped_path.exists():
        try:
            payload = json.loads(dropped_path.read_text())
        except json.JSONDecodeError:
            payload = {}

    user_drops = payload.setdefault("dropped_by_user", [])
    now = datetime.now(UTC).isoformat()
    for comment in to_record:
        user_drops.append({
            "id": comment.id,
            "reason": reason,
            "dismissed_at": now,
            "comment": comment.model_dump(mode="json"),
        })

    dropped_path.write_text(json.dumps(payload, indent=2, default=str))
    return len(to_record)
