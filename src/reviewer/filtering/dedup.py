"""Collapse near-duplicate comments.

MVP heuristic: cluster by (file, 5-line window, category) and keep the
highest-confidence comment per cluster. Production would cluster on embedding
similarity of the comment body — that catches "different lines, same point"
which this heuristic misses by design.
"""

from __future__ import annotations

from collections import defaultdict

from ..models import ReviewComment

PROXIMITY_WINDOW = 5


def dedupe_by_location(
    comments: list[ReviewComment],
) -> tuple[list[ReviewComment], list[ReviewComment]]:
    """Returns (kept, dropped). Each kept comment is the strongest in its cluster."""
    buckets: dict[tuple, list[ReviewComment]] = defaultdict(list)
    for c in comments:
        key = (c.file_path, c.line // PROXIMITY_WINDOW, c.category)
        buckets[key].append(c)

    kept: list[ReviewComment] = []
    dropped: list[ReviewComment] = []
    for group in buckets.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        group.sort(key=lambda c: c.confidence, reverse=True)
        kept.append(group[0])
        dropped.extend(group[1:])
    return kept, dropped
