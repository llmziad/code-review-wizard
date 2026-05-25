"""AST-aware diff parsing.

Input: a unified diff (text) plus a checked-out repo at the head SHA.
Output: a list of `ChangedSymbol` (function/method/class) for the changed
ranges, plus a per-file map of post-image line numbers that are anchorable
in GitHub's review API.

Why symbols, not lines:
A line diff answers "what bytes moved." A symbol diff answers "what behavior
changed." Reviews are about behavior. By lifting to the symbol level here, the
downstream LLM pass can reason about whole functions instead of dangling line
fragments — which is the difference between "this loop looks weird" and
"this function's invariant broke."
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from tree_sitter_languages import get_parser

from ..models import ChangedSymbol
from ..models import Language as LangLiteral

_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@"
)

EXT_TO_LANG: dict[str, LangLiteral] = {
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".js": "javascript",
    ".jsx": "jsx",
}

# Tree-sitter node types we treat as symbols, per language.
# (Python's function-vs-method distinction is decided at walk-time by parent context.)
SYMBOL_NODE_TYPES: dict[str, dict[str, str]] = {
    "python": {
        "function_definition": "function",
        "class_definition": "class",
    },
    "typescript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "javascript": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "tsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
    "jsx": {
        "function_declaration": "function",
        "class_declaration": "class",
        "method_definition": "method",
    },
}


# ---------------------------------------------------------------------------
# Unified diff parser (internal model)
# ---------------------------------------------------------------------------


@dataclass
class Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str] = field(default_factory=list)  # raw lines incl. +/- prefix


@dataclass
class FileDiff:
    old_path: str
    new_path: str
    hunks: list[Hunk] = field(default_factory=list)

    @property
    def is_addition(self) -> bool:
        return self.old_path == "/dev/null"

    @property
    def is_deletion(self) -> bool:
        return self.new_path == "/dev/null"


def parse_unified_diff(diff_text: str) -> list[FileDiff]:
    """Split a unified diff into per-file FileDiff records.

    Tolerates extras like 'index', 'similarity index', 'new file mode' lines
    and skips over binary-diff stanzas (which have no hunks anyway).
    """
    files: list[FileDiff] = []
    cur_file: FileDiff | None = None
    cur_hunk: Hunk | None = None

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            cur_file = None
            cur_hunk = None
            continue
        if line.startswith("--- "):
            old = line[4:]
            old_path = old[2:] if old.startswith("a/") else old
            cur_file = FileDiff(old_path=old_path, new_path="")
            files.append(cur_file)
            cur_hunk = None
            continue
        if line.startswith("+++ ") and cur_file is not None:
            new = line[4:]
            cur_file.new_path = new[2:] if new.startswith("b/") else new
            continue
        m = _HUNK_HEADER_RE.match(line)
        if m and cur_file is not None:
            old_start, old_count, new_start, new_count = m.groups()
            cur_hunk = Hunk(
                old_start=int(old_start),
                old_count=int(old_count) if old_count is not None else 1,
                new_start=int(new_start),
                new_count=int(new_count) if new_count is not None else 1,
            )
            cur_file.hunks.append(cur_hunk)
            continue
        if cur_hunk is not None and line.startswith(("+", "-", " ")):
            cur_hunk.lines.append(line)
        elif cur_hunk is not None and line == "":
            # Empty context line — preserve as a space-prefixed line.
            cur_hunk.lines.append(" ")
    return files


def collect_diff_line_anchors(fd: FileDiff) -> list[int]:
    """Post-image line numbers present in the diff (added or context).

    These are the only lines GitHub will accept as line anchors for a review
    comment on this file. Pure-deletion lines have no anchor in the post-image.
    """
    anchors: list[int] = []
    for hunk in fd.hunks:
        new_line = hunk.new_start
        for raw in hunk.lines:
            if raw.startswith("+") or raw.startswith(" "):
                anchors.append(new_line)
                new_line += 1
            # "-" lines: deletion, no new-line advance
    return anchors


def changed_post_lines(fd: FileDiff) -> set[int]:
    """Just the post-image lines that were *added* — the lines we want reviewed."""
    changed: set[int] = set()
    for hunk in fd.hunks:
        new_line = hunk.new_start
        for raw in hunk.lines:
            if raw.startswith("+"):
                changed.add(new_line)
                new_line += 1
            elif raw.startswith(" "):
                new_line += 1
    return changed


def detect_language(file_path: str) -> LangLiteral:
    return EXT_TO_LANG.get(Path(file_path).suffix.lower(), "unknown")


# ---------------------------------------------------------------------------
# Symbol extraction via tree-sitter
# ---------------------------------------------------------------------------


def extract_changed_symbols(
    file_diffs: list[FileDiff], repo_root: Path
) -> list[ChangedSymbol]:
    """Map each changed line to its smallest enclosing function/method/class.

    Files we can't parse (unknown language, missing on disk, decode errors)
    fall back to one file-level pseudo-symbol so the downstream pass still
    sees them — silently dropping changes is worse than coarse context.
    """
    out: list[ChangedSymbol] = []
    for fd in file_diffs:
        if fd.is_deletion:
            continue  # no post-image to anchor against
        post_path = fd.new_path
        if not post_path:
            continue

        full = repo_root / post_path
        if not full.exists() or not full.is_file():
            continue

        language = detect_language(post_path)
        try:
            source = full.read_text(errors="replace")
        except OSError:
            continue

        changed = changed_post_lines(fd)
        if not changed:
            continue

        if language == "unknown" or language not in SYMBOL_NODE_TYPES:
            out.append(_file_level_symbol(post_path, source))
            continue

        out.extend(
            _symbols_enclosing(
                source=source,
                language=language,
                changed_lines=changed,
                file_path=post_path,
            )
        )
    return out


def _file_level_symbol(file_path: str, source: str) -> ChangedSymbol:
    line_count = max(source.count("\n") + 1, 1)
    return ChangedSymbol(
        file_path=file_path,
        symbol_name=Path(file_path).name,
        symbol_kind="function",  # placeholder kind for non-symbolic files
        start_line=1,
        end_line=line_count,
        new_source=source[:8000],  # cap to keep token budget bounded
        language="unknown",
    )


def _symbols_enclosing(
    *,
    source: str,
    language: LangLiteral,
    changed_lines: set[int],
    file_path: str,
) -> list[ChangedSymbol]:
    parser = get_parser(language)
    tree = parser.parse(source.encode("utf-8"))

    candidates: list[tuple[int, int, str, str]] = []
    _collect_symbol_nodes(tree.root_node, language, candidates, in_class=False)

    chosen: dict[tuple[int, int, str], str] = {}
    for line in changed_lines:
        enclosing = [c for c in candidates if c[0] <= line <= c[1]]
        if not enclosing:
            continue
        enclosing.sort(key=lambda c: c[1] - c[0])  # smallest first
        start, end, kind, name = enclosing[0]
        chosen[(start, end, name)] = kind

    src_lines = source.splitlines()
    result: list[ChangedSymbol] = []
    for (start, end, name), kind in chosen.items():
        body = "\n".join(src_lines[start - 1 : end])
        result.append(
            ChangedSymbol(
                file_path=file_path,
                symbol_name=name,
                symbol_kind=kind,  # type: ignore[arg-type]
                start_line=start,
                end_line=end,
                new_source=body,
                language=language,
            )
        )
    return result


def list_symbol_names(file_path: Path, language: LangLiteral) -> list[str]:
    """Names of every top-level + nested function/method/class in a file.

    Used by the symbol resolver to compute sibling context. Cheap: one parse
    per file, no traversal of the file's text beyond what tree-sitter does.
    """
    if language not in SYMBOL_NODE_TYPES:
        return []
    try:
        source = file_path.read_text(errors="replace")
    except OSError:
        return []
    parser = get_parser(language)
    tree = parser.parse(source.encode("utf-8"))
    out: list[tuple[int, int, str, str]] = []
    _collect_symbol_nodes(tree.root_node, language, out, in_class=False)
    return [name for (_s, _e, _k, name) in out]


def _collect_symbol_nodes(
    node, language: LangLiteral, out: list, in_class: bool
) -> None:
    type_map = SYMBOL_NODE_TYPES[language]
    if node.type in type_map:
        kind = type_map[node.type]
        if language == "python" and node.type == "function_definition" and in_class:
            kind = "method"
        name_node = node.child_by_field_name("name")
        if name_node is not None and name_node.text:
            name = name_node.text.decode("utf-8", errors="replace")
            out.append(
                (node.start_point[0] + 1, node.end_point[0] + 1, kind, name)
            )
    is_class = node.type in ("class_definition", "class_declaration")
    for child in node.children:
        _collect_symbol_nodes(child, language, out, in_class=in_class or is_class)


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def parse(diff_text: str, repo_root: Path) -> tuple[list[ChangedSymbol], dict[str, list[int]]]:
    """Parse a unified diff against a checked-out repo.

    Returns:
        symbols: one ChangedSymbol per (file, enclosing-symbol) pair that had
            at least one added line.
        anchors: per-file list of post-image lines that exist in the diff.
            These are the lines a ReviewComment may legally point at.
    """
    file_diffs = parse_unified_diff(diff_text)
    symbols = extract_changed_symbols(file_diffs, repo_root)
    anchors = {
        fd.new_path: collect_diff_line_anchors(fd)
        for fd in file_diffs
        if not fd.is_deletion and fd.new_path
    }
    return symbols, anchors
