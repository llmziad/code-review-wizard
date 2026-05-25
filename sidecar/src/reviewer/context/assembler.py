"""Stage 1 orchestrator — PR URL → fully populated ContextBundle.

The single function downstream code should care about is `assemble`. Every
intermediate (diff parsing, symbol enrichment, conventions retrieval) is
encapsulated here so analysis passes can stay focused on the LLM call.
"""

from __future__ import annotations

from ..github_client import GitHubClient
from ..models import ContextBundle
from .conventions import get_conventions_snippet
from .diff_parser import parse
from .symbol_resolver import enrich_all


def assemble(url: str, gh: GitHubClient) -> ContextBundle:
    pr = gh.fetch_pr_metadata(url)
    diff = gh.fetch_diff(pr)
    repo = gh.clone_repo(pr)

    symbols, anchors = parse(diff, repo)
    contexts = enrich_all(symbols, repo)
    conventions = get_conventions_snippet(contexts)

    return ContextBundle(
        pr=pr,
        changed_symbols=contexts,
        repo_conventions_snippet=conventions,
        diff_summary=_summarize(contexts, anchors),
        full_diff=diff,
        diff_line_anchors=anchors,
    )


def _summarize(contexts, anchors) -> str:
    files = len(anchors)
    syms = len(contexts)
    lines = sum(len(v) for v in anchors.values())
    return f"{files} file(s) · {syms} changed symbol(s) · {lines} anchorable line(s)"
