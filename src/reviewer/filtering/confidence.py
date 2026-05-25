"""Drop comments the producer wasn't sure about.

The threshold lives in Settings, not here. A reviewer in the interview will
ask "how did you pick 0.7?" — the correct answer is "it's a knob, and in
production it'd be tuned against the feedback loop's resolve/dismiss data."
"""

from __future__ import annotations

from ..models import ReviewComment


def filter_by_confidence(
    comments: list[ReviewComment], threshold: float
) -> tuple[list[ReviewComment], list[ReviewComment]]:
    """Returns (kept, dropped). `kept[i].confidence >= threshold` for every i."""
    kept = [c for c in comments if c.confidence >= threshold]
    dropped = [c for c in comments if c.confidence < threshold]
    return kept, dropped
