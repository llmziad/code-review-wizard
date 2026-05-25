"""Diff parser — offline tests against the recorded PR fixture.

These tests exercise the pure-diff side of the parser: hunk extraction,
anchor-line computation, added-line set. Symbol extraction requires a checked-
out repo on disk and is covered by the live development runs (see the
fixture-capture commands in scripts not committed here) rather than by CI,
since we don't want every test run to shell out to git.
"""

from __future__ import annotations

from reviewer.context.diff_parser import (
    changed_post_lines,
    collect_diff_line_anchors,
    detect_language,
    parse_unified_diff,
)


def test_parse_pr_1244_finds_two_files(pr_1244_diff: str) -> None:
    files = parse_unified_diff(pr_1244_diff)
    paths = sorted(fd.new_path for fd in files)
    assert paths == ["src/anthropic/_client.py", "tests/test_client.py"]


def test_parse_pr_1244_has_one_hunk_each(pr_1244_diff: str) -> None:
    files = parse_unified_diff(pr_1244_diff)
    assert all(len(fd.hunks) == 1 for fd in files), \
        "PR 1244 has exactly one hunk per file; if this fails the fixture changed"


def test_anchors_match_added_and_context_lines(pr_1244_diff: str) -> None:
    """Anchor lines are added + context (everything in the post-image of the diff)."""
    files = parse_unified_diff(pr_1244_diff)
    client_fd = next(fd for fd in files if fd.new_path == "src/anthropic/_client.py")
    anchors = collect_diff_line_anchors(client_fd)
    # The recorded hunk starts at line 513 and contains 18 post-image lines
    assert anchors[0] == 513
    assert anchors[-1] == 530
    assert len(anchors) == 18


def test_changed_post_lines_is_subset_of_anchors(pr_1244_diff: str) -> None:
    """Added lines (the lines we care to review) are a subset of all anchor lines."""
    for fd in parse_unified_diff(pr_1244_diff):
        added = changed_post_lines(fd)
        anchors = set(collect_diff_line_anchors(fd))
        assert added.issubset(anchors), f"added lines escaped the anchor set in {fd.new_path}"


def test_detect_language_by_extension() -> None:
    assert detect_language("src/foo.py") == "python"
    assert detect_language("ui/Button.tsx") == "tsx"
    assert detect_language("util.ts") == "typescript"
    assert detect_language("style.css") == "unknown"
    assert detect_language("Makefile") == "unknown"


def test_empty_diff_yields_no_files() -> None:
    assert parse_unified_diff("") == []


def test_handles_pure_context_hunk() -> None:
    """A hunk with only context lines still parses; anchors equal context lines."""
    diff = (
        "diff --git a/x.py b/x.py\n"
        "--- a/x.py\n"
        "+++ b/x.py\n"
        "@@ -1,3 +1,3 @@\n"
        " line1\n"
        " line2\n"
        " line3\n"
    )
    fds = parse_unified_diff(diff)
    assert len(fds) == 1
    assert collect_diff_line_anchors(fds[0]) == [1, 2, 3]
    assert changed_post_lines(fds[0]) == set()
