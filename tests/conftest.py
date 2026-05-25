"""Shared fixtures.

We keep fixtures small and explicit. The only "fancy" thing is loading the
recorded PR fixture once per test session via lru_cache so multiple tests
can share a parsed bundle without re-reading disk.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@lru_cache(maxsize=4)
def _load_fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def pr_1244_diff() -> str:
    """Unified diff text for anthropics/anthropic-sdk-python PR #1244.

    Real diff, recorded at fixture-capture time. The PR adds 413 and 529
    error-code handling to `_make_status_error` plus a parity test.
    """
    return _load_fixture_text("pr_1244.diff")


@pytest.fixture
def pr_1244_metadata_json() -> str:
    return _load_fixture_text("pr_1244_metadata.json")
