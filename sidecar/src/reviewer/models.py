"""Pydantic models — the contract every module honors.

Every cross-module boundary in this system passes data via these models. If a
type crosses a module boundary, it lives here. Module-internal scratch types
live next to their owning module.

The model layer is also the place where invariants get enforced. A model that
accepts garbage is a leak; a model that rejects garbage at the boundary keeps
downstream code simple.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Type aliases — used wherever a constrained string would otherwise drift.
# ---------------------------------------------------------------------------

SymbolKind = Literal["function", "method", "class"]
Language = Literal["python", "typescript", "javascript", "tsx", "jsx", "unknown"]
Category = Literal["readability", "structure", "maintainability", "correctness"]
Severity = Literal["blocking", "suggestion", "nit"]

_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$")


def _validate_sha(value: str) -> str:
    if not _SHA_RE.match(value):
        raise ValueError(f"not a git SHA: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Stage 0 — PR identity
# ---------------------------------------------------------------------------


class PRSummary(BaseModel):
    """Lightweight PR record for the list/discovery surface.

    Built from GitHub's search API result, which returns issue-shaped objects.
    Notably absent: additions, deletions, changed_files — those require a per-PR
    fetch and we keep listing cheap. The full PRMetadata is what `review` and
    `inspect` build for a specific PR.
    """

    url: str  # html_url
    owner: str
    repo: str
    number: int = Field(gt=0)
    title: str
    author: str
    created_at: datetime
    updated_at: datetime
    state: Literal["open", "closed"] = "open"
    draft: bool = False


class PRMetadata(BaseModel):
    """Stable identity of a PR. Carried unchanged through every stage."""

    owner: str
    repo: str
    number: int = Field(gt=0)
    title: str
    description: str = ""
    author: str
    base_sha: str
    head_sha: str
    url: str

    @field_validator("base_sha", "head_sha")
    @classmethod
    def _check_sha(cls, v: str) -> str:
        return _validate_sha(v)


# ---------------------------------------------------------------------------
# Stage 1 — Context assembly
# ---------------------------------------------------------------------------


class ChangedSymbol(BaseModel):
    """A function/method/class touched by the diff.

    We operate on symbols, not raw line ranges. A line diff answers
    "what bytes moved." A symbol diff answers "what behavior changed."
    Reviews are about behavior.
    """

    file_path: str
    symbol_name: str
    symbol_kind: SymbolKind
    start_line: int = Field(gt=0, description="1-indexed line in the post-image file")
    end_line: int = Field(gt=0)
    old_source: str | None = None
    new_source: str
    language: Language

    @model_validator(mode="after")
    def _check_line_order(self) -> ChangedSymbol:
        if self.end_line < self.start_line:
            raise ValueError("end_line must be >= start_line")
        return self


class CallSite(BaseModel):
    """A place outside the changed symbol where it is referenced or called."""

    file_path: str
    line: int = Field(gt=0)
    snippet: str
    caller_symbol: str | None = None
    is_test: bool = False


class SymbolContext(BaseModel):
    """A ChangedSymbol enriched with surrounding context."""

    symbol: ChangedSymbol
    callers: list[CallSite] = Field(default_factory=list)
    sibling_symbols: list[str] = Field(default_factory=list)
    related_test_files: list[str] = Field(default_factory=list)


class ContextBundle(BaseModel):
    """Everything one analysis pass needs. The output of Stage 1.

    `diff_line_anchors` lists the post-image lines that appear in the diff
    for each file. These are the only lines a ReviewComment can be anchored
    to — GitHub's review API will reject comments on lines outside the diff.
    The bundle carries this set explicitly so the prompt can show the model
    what's valid and so post-LLM validation can route orphans to the summary.
    """

    pr: PRMetadata
    changed_symbols: list[SymbolContext]
    repo_conventions_snippet: str
    diff_summary: str
    full_diff: str = ""
    diff_line_anchors: dict[str, list[int]] = Field(default_factory=dict)

    def valid_lines_for(self, file_path: str) -> set[int]:
        return set(self.diff_line_anchors.get(file_path, []))


# ---------------------------------------------------------------------------
# Stage 2 — Analysis output
# ---------------------------------------------------------------------------


class ReviewComment(BaseModel):
    """One candidate review comment, before filtering and posting.

    `extra="forbid"` keeps the LLM tool-use contract tight: any drift between
    the prompt's tool schema and this model is a loud failure, not a silent one.
    """

    model_config = ConfigDict(extra="forbid")

    file_path: str
    line: int = Field(gt=0)
    category: Category
    severity: Severity
    title: str
    body: str
    confidence: float = Field(ge=0.0, le=1.0)
    pass_name: str
    suggested_fix: str | None = None


class PassResult(BaseModel):
    pass_name: str
    comments: list[ReviewComment] = Field(default_factory=list)
    tokens_used_input: int = 0
    tokens_used_output: int = 0
    duration_ms: int = 0
    error: str | None = None


# ---------------------------------------------------------------------------
# Stage 4 — Final report
# ---------------------------------------------------------------------------


class CostSummary(BaseModel):
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    by_pass: dict[str, dict[str, float]] = Field(default_factory=dict)


PipelineStage = Literal["context", "analysis", "filtering"]
PipelineEventKind = Literal[
    "started",
    "progress",
    "pass_started",
    "pass_completed",
    "completed",
]


class PipelineEvent(BaseModel):
    """One progress signal from the streaming pipeline.

    The streaming generator emits these between every meaningful step so the
    desktop UI (and any CLI watcher) can render real-time progress instead of
    staring at a 10-second blank wait.

    Field meanings by event kind:
        started / completed   — stage boundary
        progress              — within-stage milestone (detail describes it)
        pass_started          — Stage 2 only; `name` is the pass name
        pass_completed        — Stage 2 only; tokens_* and duration_ms set
    """

    stage: PipelineStage
    event: PipelineEventKind
    detail: str | None = None
    name: str | None = None  # set for pass_* events
    tokens_input: int | None = None
    tokens_output: int | None = None
    duration_ms: int | None = None


class ReviewReport(BaseModel):
    """Filtered, prioritized output of Stage 3 — what Stage 4 delivers.

    `comments` are inline-anchored to specific diff lines.
    `orphan_comments` are out-of-diff comments rolled into the summary body;
    GitHub can't line-anchor them, so they ride along in prose instead of
    being silently dropped.
    """

    pr: PRMetadata
    comments: list[ReviewComment] = Field(default_factory=list)
    orphan_comments: list[ReviewComment] = Field(default_factory=list)
    summary: str = ""
    passes_run: list[str] = Field(default_factory=list)
    passes_skipped: list[str] = Field(default_factory=list)
    total_candidates: int = 0
    total_posted: int = 0
    cost_summary: CostSummary = Field(default_factory=CostSummary)
    run_id: str | None = None  # set by pipeline when persisted; used by post_review / dismiss
