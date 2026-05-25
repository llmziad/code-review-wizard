"""Stage 3 — filtering pipeline.

Three sequential filters, each pure on its input:

  raw comments
     ↓
  confidence (drop below threshold)
     ↓
  dedup (collapse near-duplicates by location)
     ↓
  prioritize (sort by severity+confidence, cap per severity)
     ↓
  surviving comments + per-stage drop log

The drop log is the substrate of the (future) feedback loop: paired with the
resolve/dismiss signals from GitHub, it teaches the filter where the threshold
should actually live.
"""

from __future__ import annotations

from ..config import Settings
from ..models import ReviewComment
from .confidence import filter_by_confidence
from .dedup import dedupe_by_location
from .prioritizer import prioritize


def apply_all(
    comments: list[ReviewComment], settings: Settings
) -> tuple[list[ReviewComment], dict[str, list[ReviewComment]]]:
    """Run the full filter pipeline. Returns (survivors, dropped_by_stage)."""
    after_conf, dropped_conf = filter_by_confidence(comments, settings.min_confidence)
    after_dedup, dropped_dedup = dedupe_by_location(after_conf)
    final, dropped_priority = prioritize(after_dedup, settings)
    return final, {
        "dropped_by_confidence": dropped_conf,
        "dropped_by_dedup": dropped_dedup,
        "dropped_by_priority": dropped_priority,
    }


__all__ = [
    "apply_all",
    "filter_by_confidence",
    "dedupe_by_location",
    "prioritize",
]
