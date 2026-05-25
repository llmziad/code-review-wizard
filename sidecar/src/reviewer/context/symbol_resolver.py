"""Symbol resolution — for each ChangedSymbol, find external callers and siblings.

Strategy:
- Callers: ripgrep word-bounded for the symbol name across the repo. For each
  hit, a coarse regex confirms the line looks like a call site (`name(`), not
  a string literal or type annotation. Cap at 5 per symbol to bound token cost.
- Siblings: tree-sitter walk the defining file once; emit the names of other
  symbols. Free intel — same parse cost as the symbol enumeration we did in
  diff_parser, but in a different file.

Why ripgrep + regex instead of a full call-graph tool (Jedi, ts-morph):
- Language-agnostic with one code path.
- Sub-second on any reasonably-sized repo.
- False positives (string mentions of the name) are filtered by the regex; the
  remaining noise is bounded by the 5-per-symbol cap.
- The interview talking point: "We don't need a perfect call graph for review.
  We need *good enough* context for the model to reason about blast radius."
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..models import CallSite, ChangedSymbol, SymbolContext
from .diff_parser import detect_language, list_symbol_names

MAX_CALLERS_PER_SYMBOL = 5
MAX_NON_TEST_CALLERS = 3
MAX_TEST_CALLERS = 2
RIPGREP_TIMEOUT_SECONDS = 15

# Coarse pattern: `name` followed by optional whitespace then `(`.
# Filters out: `from x import name`, type annotations like `x: Name = ...`,
# string mentions, and most comments.
def _call_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"\b{re.escape(name)}\b\s*\(")


# Definition lines look like calls to the regex above but aren't.
# We strip them so "callers" actually means callers, not other definitions.
def _definition_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"\b(?:def|function|fn|async\s+def)\s+{re.escape(name)}\b")


_TEST_PATH_HINTS = ("/test/", "/tests/", "/__tests__/", "test_", ".test.", "_test.")


def _looks_like_test(rel_path: str) -> bool:
    low = rel_path.lower()
    return any(hint in low for hint in _TEST_PATH_HINTS)


def _all_call_sites(symbol: ChangedSymbol, repo_root: Path) -> list[CallSite]:
    """Internal: every confirmed call site of `symbol` in the repo, uncapped.

    The caller (enrich) chooses how to budget src vs. test sites.
    """
    if not symbol.symbol_name or not symbol.symbol_name.isidentifier():
        return []

    try:
        proc = subprocess.run(
            [
                "rg",
                "--json",
                "--word-regexp",
                "--no-heading",
                symbol.symbol_name,
                str(repo_root),
            ],
            capture_output=True,
            text=True,
            timeout=RIPGREP_TIMEOUT_SECONDS,
            check=False,
        )
    except (subprocess.SubprocessError, FileNotFoundError):
        return []

    call_re = _call_pattern(symbol.symbol_name)
    def_re = _definition_pattern(symbol.symbol_name)
    confirmed: list[CallSite] = []
    seen: set[tuple[str, int]] = set()

    for raw in proc.stdout.splitlines():
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if event.get("type") != "match":
            continue

        data = event["data"]
        abs_path = Path(data["path"]["text"])
        try:
            rel_path = str(abs_path.relative_to(repo_root))
        except ValueError:
            continue

        if rel_path == symbol.file_path:
            continue
        if detect_language(rel_path) == "unknown":
            continue

        line_no = data["line_number"]
        line_text = data["lines"]["text"].rstrip("\n")

        if def_re.search(line_text):
            continue  # this is another definition (override / overload), not a call
        if not call_re.search(line_text):
            continue

        key = (rel_path, line_no)
        if key in seen:
            continue
        seen.add(key)

        confirmed.append(
            CallSite(
                file_path=rel_path,
                line=line_no,
                snippet=line_text.strip()[:200],
                is_test=_looks_like_test(rel_path),
            )
        )

    return confirmed


def find_callers(symbol: ChangedSymbol, repo_root: Path) -> list[CallSite]:
    """Public: up to MAX_NON_TEST_CALLERS production callers + MAX_TEST_CALLERS test callers.

    Mixing the two gives the LLM both "where this lives" and "how this is exercised"
    in one budget, instead of letting alphabetical-file-order eat all the slots.
    """
    all_sites = _all_call_sites(symbol, repo_root)
    non_test = [c for c in all_sites if not c.is_test][:MAX_NON_TEST_CALLERS]
    test = [c for c in all_sites if c.is_test][:MAX_TEST_CALLERS]
    return non_test + test


def find_sibling_symbols(symbol: ChangedSymbol, repo_root: Path) -> list[str]:
    """Names of other symbols defined in the same file."""
    full = repo_root / symbol.file_path
    if not full.exists():
        return []
    language = detect_language(symbol.file_path)
    names = list_symbol_names(full, language)
    # Dedupe and exclude the symbol itself
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        if n == symbol.symbol_name or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def enrich(symbol: ChangedSymbol, repo_root: Path) -> SymbolContext:
    """Build a SymbolContext for one ChangedSymbol.

    Runs ripgrep once and reuses the result for both `callers` (budgeted mix
    of src + test) and `related_test_files` (every test file that touched it).
    """
    all_sites = _all_call_sites(symbol, repo_root)
    non_test = [c for c in all_sites if not c.is_test][:MAX_NON_TEST_CALLERS]
    test = [c for c in all_sites if c.is_test][:MAX_TEST_CALLERS]
    callers = non_test + test
    related_tests = sorted({c.file_path for c in all_sites if c.is_test})[:5]

    return SymbolContext(
        symbol=symbol,
        callers=callers,
        sibling_symbols=find_sibling_symbols(symbol, repo_root),
        related_test_files=related_tests,
    )


def enrich_all(symbols: list[ChangedSymbol], repo_root: Path) -> list[SymbolContext]:
    return [enrich(s, repo_root) for s in symbols]
