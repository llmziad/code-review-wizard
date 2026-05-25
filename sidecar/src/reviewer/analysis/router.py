"""Pass routing — decide which analysis passes to run for a given bundle.

MVP behavior: always run [MaintainabilityPass]. This is the hardcoded baseline.

Production behavior (designed-for, not implemented): a tiny classifier looks at
diff features — file extensions, change size, presence of test edits, presence
of auth/payment/security keywords — and decides which subset of passes earn
their token cost. Most PRs need 1–2 passes, not all of them.

Keeping the router as a separate file means the upgrade path is local: replace
this file's `pick_passes` body, no other module changes.
"""

from __future__ import annotations

from ..config import Settings, get_settings
from ..models import ContextBundle
from .base import AnalysisPass
from .maintainability import MaintainabilityPass


def pick_passes(bundle: ContextBundle, settings: Settings | None = None) -> list[AnalysisPass]:
    """Return the passes to run on this bundle. MVP: always [maintainability]."""
    settings = settings or get_settings()
    return [MaintainabilityPass(settings=settings)]
