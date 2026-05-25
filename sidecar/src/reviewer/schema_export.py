"""JSON Schema export — the seam for the Electron app's TypeScript types.

The desktop app's build step runs `reviewer schema --out app/src/shared/types.ts`,
which pipes this module's output through quicktype to produce typed
interfaces for every payload the sidecar can return. One source of truth
across Python ↔ TypeScript; drift fails at the type checker.

Pydantic walks model references transitively, so we only have to enumerate
the top-level types each sidecar method returns. Supporting types (PRMetadata,
ChangedSymbol, etc.) ride along under $defs automatically.
"""

from __future__ import annotations

from pydantic.json_schema import models_json_schema

from .auth import DeviceFlowChallenge, StoredAuth
from .models import (
    ContextBundle,
    PipelineEvent,
    PRSummary,
    ReviewReport,
)

# One entry per RPC response shape. Don't add types that are purely internal
# (PassResult, CostSummary, etc.) — they're pulled in transitively if the
# top-level types reference them.
_PUBLIC_MODELS = [
    PRSummary,        # list_prs
    ContextBundle,    # inspect_pr
    ReviewReport,     # review_pr (final), get_run, post_review (input)
    PipelineEvent,    # review_pr (streamed events)
    StoredAuth,       # login_pat, login_device_poll
    DeviceFlowChallenge,  # login_device_start
]


def build_schema_doc(title: str = "Code Review Wizard sidecar schemas") -> dict:
    """Returns a single JSON Schema document with every public model under $defs."""
    _, combined = models_json_schema(
        [(m, "validation") for m in _PUBLIC_MODELS],
        title=title,
        description=(
            "Auto-generated from the sidecar's Pydantic models. "
            "Consumed by the desktop app's build step (quicktype → TypeScript)."
        ),
    )
    return combined
