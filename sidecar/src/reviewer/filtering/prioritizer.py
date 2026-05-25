"""Sort by severity then confidence, then cap per-severity.

Caps come from Settings and are defensible numbers: a human reviewer can
realistically act on ~3 blocking, ~5 suggestions, ~3 nits in one pass.
Beyond that, you're triggering a "mute the bot" reflex.
"""

from __future__ import annotations

from ..config import Settings
from ..models import ReviewComment, Severity

_SEVERITY_RANK: dict[Severity, int] = {"blocking": 0, "suggestion": 1, "nit": 2}


def prioritize(
    comments: list[ReviewComment], settings: Settings
) -> tuple[list[ReviewComment], list[ReviewComment]]:
    """Returns (kept, dropped). `kept` is sorted in display order."""
    ordered = sorted(comments, key=lambda c: (_SEVERITY_RANK[c.severity], -c.confidence))

    caps: dict[Severity, int] = {
        "blocking": settings.max_blocking_comments,
        "suggestion": settings.max_suggestion_comments,
        "nit": settings.max_nit_comments,
    }
    counts: dict[Severity, int] = {"blocking": 0, "suggestion": 0, "nit": 0}

    kept: list[ReviewComment] = []
    dropped: list[ReviewComment] = []
    for c in ordered:
        if counts[c.severity] < caps[c.severity]:
            kept.append(c)
            counts[c.severity] += 1
        else:
            dropped.append(c)
    return kept, dropped
