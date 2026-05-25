"""The maintainability pass — the one real LLM call in the MVP.

What's interesting in this file (interview talking points):

1. **Structured output via tool-use, not prose JSON.** The model returns
   `tool_use` blocks whose input is validated against the `ReviewComment`
   schema. This is the single biggest reliability win available for
   structured LLM output: no JSON-parsing regex, no markdown fences to strip,
   no "the model decided to wrap it in code today."

2. **Tool schema is derived from a slim version of `ReviewComment`.** We
   don't ship internal fields (`pass_name`) to the model — fewer fields to
   set means fewer fields to hallucinate.

3. **In-diff anchoring is enforced.** GitHub's review API only line-anchors
   to lines that are part of the diff. The prompt tells the model this,
   and post-LLM we split comments into `inline` (anchored) and `orphan`
   (folded into the review summary) instead of silently dropping orphans.

4. **Retries are status-aware, not blind.** 429/529/5xx → exponential
   backoff. Everything else → fail fast.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

from anthropic import (
    Anthropic,
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import ValidationError

from ..config import Settings, get_settings
from ..models import ContextBundle, PassResult, ReviewComment, SymbolContext
from .base import AnalysisPass

_PROMPT_PATH = Path(__file__).parent / "prompts" / "maintainability.md"
_MAX_TOKENS = 4096
_MAX_RETRIES = 4

# Categories THIS pass is allowed to emit. (The data model permits more; this
# pass restricts itself so a future correctness pass can own "correctness".)
_PASS_CATEGORIES = ["readability", "structure", "maintainability"]


class MaintainabilityPass(AnalysisPass):
    name = "maintainability"

    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        if not self.settings.anthropic_api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY is not set. Add it to .env or the environment."
            )
        self._client = Anthropic(api_key=self.settings.anthropic_api_key)
        self._system_prompt = _PROMPT_PATH.read_text(encoding="utf-8")

    def should_run(self, bundle: ContextBundle) -> bool:
        return len(bundle.changed_symbols) > 0

    async def run(self, bundle: ContextBundle) -> PassResult:
        start = time.time()
        user_message = render_user_message(bundle)
        tools = [_tool_definition()]

        try:
            response = await asyncio.to_thread(
                self._call_with_retry,
                system=self._system_prompt,
                user=user_message,
                tools=tools,
            )
        except Exception as e:  # bubble up as PassResult.error
            return PassResult(
                pass_name=self.name,
                error=f"{type(e).__name__}: {e}",
                duration_ms=int((time.time() - start) * 1000),
            )

        comments = _extract_comments(response, pass_name=self.name)
        usage = getattr(response, "usage", None)
        return PassResult(
            pass_name=self.name,
            comments=comments,
            tokens_used_input=getattr(usage, "input_tokens", 0) if usage else 0,
            tokens_used_output=getattr(usage, "output_tokens", 0) if usage else 0,
            duration_ms=int((time.time() - start) * 1000),
        )

    def _call_with_retry(self, *, system: str, user: str, tools: list[dict]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                return self._client.messages.create(
                    model=self.settings.anthropic_model,
                    max_tokens=_MAX_TOKENS,
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    tools=tools,
                    tool_choice={"type": "tool", "name": "submit_review_comments"},
                )
            except (RateLimitError, APIConnectionError, APITimeoutError) as e:
                last_exc = e
            except APIStatusError as e:
                # 529 (overloaded) and 5xx are retryable; 4xx other than 429 are not.
                if e.status_code in (529, 500, 502, 503, 504):
                    last_exc = e
                else:
                    raise

            sleep_for = min(2**attempt, 8)
            time.sleep(sleep_for)

        assert last_exc is not None
        raise last_exc


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------


def render_user_message(bundle: ContextBundle) -> str:
    """Render the ContextBundle into the human message sent to the model."""
    pr = bundle.pr
    parts: list[str] = [
        f"# PR #{pr.number} — {pr.owner}/{pr.repo}",
        f"**Title:** {pr.title}",
        f"**Author:** {pr.author}",
    ]
    if pr.description.strip():
        parts.append("**Description:**\n\n" + _truncate(pr.description, 1200))

    parts += [
        "",
        "## Anchorable diff lines",
        _format_anchors(bundle.diff_line_anchors),
        "",
        "## Full diff",
        "```diff",
        _truncate(bundle.full_diff, 12000),
        "```",
        "",
        "## Changed symbols (post-PR)",
    ]
    for ctx in bundle.changed_symbols:
        parts.append(_format_symbol(ctx))

    parts += [
        "",
        "## Repo conventions",
        bundle.repo_conventions_snippet,
        "",
        "---",
        "Review this PR per your instructions. Call the `submit_review_comments` tool.",
    ]
    return "\n".join(parts)


def _format_anchors(anchors: dict[str, list[int]]) -> str:
    if not anchors:
        return "(no anchorable lines — diff appears empty)"
    lines = []
    for path, ls in anchors.items():
        if not ls:
            continue
        # Compress consecutive runs for readability
        runs = _compress_runs(sorted(ls))
        lines.append(f"- `{path}`: {runs}")
    return "\n".join(lines)


def _compress_runs(nums: list[int]) -> str:
    if not nums:
        return ""
    runs: list[str] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        runs.append(f"{start}" if start == prev else f"{start}–{prev}")
        start = prev = n
    runs.append(f"{start}" if start == prev else f"{start}–{prev}")
    return ", ".join(runs)


def _format_symbol(ctx: SymbolContext) -> str:
    s = ctx.symbol
    parts = [
        "",
        f"### `{s.file_path}` :: `{s.symbol_name}`  "
        f"({s.symbol_kind}, {s.language}, L{s.start_line}–L{s.end_line})",
    ]
    if ctx.callers:
        parts.append("\n**Used at:**")
        for c in ctx.callers:
            tag = " (test)" if c.is_test else ""
            parts.append(f"- `{c.file_path}`:{c.line}{tag} — `{c.snippet[:120]}`")
    if ctx.sibling_symbols:
        siblings = ", ".join(f"`{n}`" for n in ctx.sibling_symbols[:12])
        more = f" (+{len(ctx.sibling_symbols) - 12} more)" if len(ctx.sibling_symbols) > 12 else ""
        parts.append(f"\n**Sibling symbols in this file:** {siblings}{more}")
    if ctx.related_test_files:
        parts.append("\n**Test files touching this symbol:** " + ", ".join(f"`{p}`" for p in ctx.related_test_files))

    parts += [
        "",
        f"```{s.language if s.language != 'unknown' else ''}",
        _truncate(s.new_source, 3000),
        "```",
    ]
    return "\n".join(parts)


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... [truncated {len(text) - limit} chars]"


# ---------------------------------------------------------------------------
# Tool definition + response parsing
# ---------------------------------------------------------------------------


def _tool_definition() -> dict:
    """JSON Schema for the submit_review_comments tool.

    We hand-author this rather than auto-derive from ReviewComment because:
    (a) we omit internal fields (`pass_name`),
    (b) we restrict `category` to this pass's allowed set,
    (c) we add per-field guidance the model can read.
    """
    return {
        "name": "submit_review_comments",
        "description": (
            "Submit your review comments. The list may be empty — that is often "
            "the right answer. Each comment must reference a file_path and line "
            "that appears in the diff."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "comments": {
                    "type": "array",
                    "description": "Review comments. Empty array is valid and common.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "file_path": {
                                "type": "string",
                                "description": "Repo-relative path of the file the comment is about.",
                            },
                            "line": {
                                "type": "integer",
                                "minimum": 1,
                                "description": "Post-image line number. Must appear in the anchorable lines for the file; if not, the comment is folded into the summary.",
                            },
                            "category": {
                                "type": "string",
                                "enum": _PASS_CATEGORIES,
                            },
                            "severity": {
                                "type": "string",
                                "enum": ["blocking", "suggestion", "nit"],
                            },
                            "title": {
                                "type": "string",
                                "description": "One-line summary, <= 80 chars.",
                            },
                            "body": {
                                "type": "string",
                                "description": "Markdown explanation: what, why it matters, what to do instead.",
                            },
                            "confidence": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0,
                                "description": "How strongly the evidence supports this comment. 0.9 = bet on it; 0.5 = depends on context I don't have.",
                            },
                            "suggested_fix": {
                                "type": "string",
                                "description": "Optional. Replacement code as a string. Omit when the fix requires judgment.",
                            },
                        },
                        "required": [
                            "file_path",
                            "line",
                            "category",
                            "severity",
                            "title",
                            "body",
                            "confidence",
                        ],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["comments"],
            "additionalProperties": False,
        },
    }


def _extract_comments(response: Any, pass_name: str) -> list[ReviewComment]:
    out: list[ReviewComment] = []
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != "submit_review_comments":
            continue
        for raw in (block.input or {}).get("comments", []):
            data = dict(raw)
            data["pass_name"] = pass_name
            # Drop empty-string suggested_fix → None for cleaner downstream rendering
            if data.get("suggested_fix") == "":
                data["suggested_fix"] = None
            try:
                out.append(ReviewComment(**data))
            except ValidationError:
                # A malformed comment is logged at the run boundary; skip here.
                continue
    return out
