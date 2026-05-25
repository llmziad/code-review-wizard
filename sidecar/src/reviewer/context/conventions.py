"""Repo convention retrieval — STUBBED for the MVP.

What this is in production:
    Embed a `CONVENTIONS.md` file plus representative examples from the repo's
    own code into a vector store. At assembly time, retrieve the chunks most
    similar to the changed symbols and return them. The result is convention
    guidance tailored to the slice of the codebase the PR is touching.

What it is here:
    A hardcoded language-aware string. Good enough to exercise the pipeline
    and to demo the interface; the architecture doc names the path to the
    real implementation.
"""

from __future__ import annotations

from ..models import SymbolContext

_PYTHON = (
    "Python conventions in this codebase:\n"
    "- PEP 8 formatting; type hints required on new functions and public methods.\n"
    "- Prefer pure functions and small modules.\n"
    "- Tests live in tests/, named `test_*.py`, run with pytest.\n"
    "- Avoid bare `except` clauses; catch specific exceptions or use `except Exception`.\n"
    "- Use `pathlib.Path` over `os.path` in new code."
)

_TS = (
    "TypeScript conventions in this codebase:\n"
    "- Airbnb style; explicit return types on exported functions.\n"
    "- Prefer composition over inheritance.\n"
    "- Tests use the framework already present in the repo (Jest, Vitest, etc.).\n"
    "- Avoid `any`; prefer generics or `unknown` with narrowing."
)

_GENERIC = (
    "General conventions:\n"
    "- Prefer clarity over cleverness.\n"
    "- Small functions, descriptive names, one responsibility per unit.\n"
    "- New behavior must have tests."
)


def get_conventions_snippet(contexts: list[SymbolContext]) -> str:
    languages = {c.symbol.language for c in contexts}
    parts: list[str] = []
    if "python" in languages:
        parts.append(_PYTHON)
    if languages & {"typescript", "tsx", "javascript", "jsx"}:
        parts.append(_TS)
    if not parts:
        parts.append(_GENERIC)
    return "\n\n".join(parts)
